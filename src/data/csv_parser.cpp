#include "data/csv_parser.h"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <fstream>
#include <stdexcept>

namespace TradingBot {
namespace {
std::string trim(std::string value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    return value.substr(first, value.find_last_not_of(" \t\r\n") - first + 1);
}
std::vector<std::string> fields(const std::string& line) {
    std::vector<std::string> result;
    std::string current;
    bool quoted = false;
    for (size_t i = 0; i < line.size(); ++i) {
        const char ch = line[i];
        if (ch == '"') {
            if (quoted && i + 1 < line.size() && line[i+1] == '"') { current += '"'; ++i; }
            else quoted = !quoted;
        } else if (ch == ',' && !quoted) { result.push_back(trim(current)); current.clear(); }
        else current += ch;
    }
    if (quoted) throw std::invalid_argument("unterminated quoted field");
    result.push_back(trim(current));
    if (result.size() != 6) throw std::invalid_argument("expected exactly timestamp,open,high,low,close,volume");
    return result;
}
double number(const std::string& value) {
    size_t consumed = 0;
    const double result = std::stod(value, &consumed);
    if (consumed != value.size() || !std::isfinite(result))
        throw std::invalid_argument("invalid finite numeric field: " + value);
    return result;
}
int digits(const std::string& value, size_t offset, size_t count) {
    int result = 0;
    for (size_t i = offset; i < offset + count; ++i) {
        if (value[i] < '0' || value[i] > '9') throw std::invalid_argument("timestamp contains non-digits");
        result = result * 10 + value[i] - '0';
    }
    return result;
}
// Gregorian calendar conversion, with epoch 1970-01-01.
std::int64_t days_from_civil(int y, unsigned m, unsigned d) {
    y -= m <= 2;
    const int era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yoe = static_cast<unsigned>(y - era * 400);
    const unsigned doy = (153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097LL + static_cast<int>(doe) - 719468;
}
void timestamp(MarketData& bar) {
    auto& value = bar.timestamp;
    value = trim(value);
    if (value.size() != 10 && value.size() != 16 && value.size() != 19)
        throw std::invalid_argument("timestamp must be YYYY-MM-DD or exchange-local YYYY-MM-DDTHH:MM[:SS]");
    if (value[4] != '-' || value[7] != '-') throw std::invalid_argument("invalid date separators");
    const int year = digits(value, 0, 4), month = digits(value, 5, 2), day = digits(value, 8, 2);
    const bool leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    const int month_days[] = {31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    if (year < 1900 || year > 9999 || month < 1 || month > 12 || day < 1 || day > month_days[month-1])
        throw std::invalid_argument("invalid Gregorian date: " + value);
    bar.session_date = value.substr(0, 10);
    bar.is_intraday = value.size() != 10;
    int hour = 0, minute = 0, second = 0;
    if (bar.is_intraday) {
        if ((value[10] != 'T' && value[10] != ' ') || value[13] != ':' || (value.size() == 19 && value[16] != ':'))
            throw std::invalid_argument("invalid intraday timestamp separators");
        hour = digits(value, 11, 2); minute = digits(value, 14, 2);
        if (value.size() == 19) second = digits(value, 17, 2);
        if (hour > 23 || minute > 59 || second > 59) throw std::invalid_argument("invalid clock time");
        value[10] = 'T';
        if (value.size() == 16) value += ":00";
        bar.minute_of_day = hour * 60 + minute;
    } else bar.minute_of_day = -1;
    bar.epoch_seconds = days_from_civil(year, month, day) * 86400 + hour * 3600 + minute * 60 + second;
}
void validate(MarketData& bar) {
    timestamp(bar);
    if (!std::isfinite(bar.open) || !std::isfinite(bar.high) || !std::isfinite(bar.low) ||
        !std::isfinite(bar.close) || !std::isfinite(bar.volume)) throw std::invalid_argument("OHLCV must be finite");
    if (bar.open <= 0 || bar.high <= 0 || bar.low <= 0 || bar.close <= 0 || bar.volume < 0)
        throw std::invalid_argument("prices must be positive and volume nonnegative");
    if (bar.high < std::max(bar.open, bar.close) || bar.low > std::min(bar.open, bar.close) || bar.low > bar.high)
        throw std::invalid_argument("inconsistent OHLC bounds");
}
}

bool CSVParser::load_data(const std::string& filename) {
    clear();
    std::ifstream stream(filename);
    if (!stream) { last_error_ = "cannot open CSV: " + filename; return false; }
    std::string line;
    size_t row = 1;
    std::vector<MarketData> records;
    try {
        if (!std::getline(stream, line)) throw std::invalid_argument("CSV is empty");
        if (line.compare(0, 3, "\xEF\xBB\xBF") == 0) line.erase(0, 3);
        auto header = fields(line);
        for (auto& field : header) std::transform(field.begin(), field.end(), field.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if ((header[0] != "timestamp" && header[0] != "date" && header[0] != "datetime") ||
            header[1] != "open" || header[2] != "high" || header[3] != "low" || header[4] != "close" || header[5] != "volume")
            throw std::invalid_argument("unsupported CSV header");
        while (std::getline(stream, line)) {
            ++row;
            if (trim(line).empty()) continue;
            const auto values = fields(line);
            MarketData bar;
            bar.timestamp = values[0]; bar.open = number(values[1]); bar.high = number(values[2]);
            bar.low = number(values[3]); bar.close = number(values[4]); bar.volume = number(values[5]);
            validate(bar);
            if (!records.empty() && (bar.epoch_seconds <= records.back().epoch_seconds || bar.is_intraday != records.front().is_intraday))
                throw std::invalid_argument("timestamps must increase strictly and use one consistent daily/intraday format");
            records.push_back(bar);
        }
        if (stream.bad()) throw std::runtime_error("CSV read failed");
        if (records.empty()) throw std::invalid_argument("CSV contains no market data");
        data_ = std::move(records);
        return true;
    } catch (const std::exception& error) {
        last_error_ = "CSV row " + std::to_string(row) + ": " + error.what();
        return false;
    }
}
bool CSVParser::load_records(const std::vector<MarketData>& records) {
    clear();
    try {
        if (records.empty()) throw std::invalid_argument("market data is empty");
        auto validated = records;
        for (size_t i = 0; i < validated.size(); ++i) {
            validate(validated[i]);
            if (i && (validated[i].epoch_seconds <= validated[i-1].epoch_seconds || validated[i].is_intraday != validated[0].is_intraday))
                throw std::invalid_argument("timestamps must increase strictly and use one consistent daily/intraday format");
        }
        data_ = std::move(validated);
        return true;
    } catch (const std::exception& error) { last_error_ = error.what(); return false; }
}
const MarketData& CSVParser::get_data(size_t index) const { return data_.at(index); }
size_t CSVParser::get_data_count() const { return data_.size(); }
std::vector<MarketData> CSVParser::get_data_range(size_t start, size_t end) const {
    if (start > end || end >= data_.size()) throw std::out_of_range("invalid inclusive market-data range");
    return {data_.begin() + start, data_.begin() + end + 1};
}
bool CSVParser::validate_data() const { return !data_.empty(); }
void CSVParser::clear() { data_.clear(); last_error_.clear(); }
bool CSVParser::valid_timestamp(const std::string& value) {
    try { MarketData bar; bar.timestamp = value; timestamp(bar); return true; }
    catch (const std::exception&) { return false; }
}
}
