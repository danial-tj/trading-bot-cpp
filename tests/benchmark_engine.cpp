#include "configuration.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <deque>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace TradingBot;
namespace {
using Clock = std::chrono::steady_clock;
double elapsed(Clock::time_point start) { return std::chrono::duration<double, std::milli>(Clock::now() - start).count(); }
double median(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    auto n = values.size(); return n % 2 ? values[n / 2] : (values[n / 2 - 1] + values[n / 2]) / 2.0;
}
std::size_t positive_integer(const std::string& value, std::size_t maximum) {
    if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos) throw std::invalid_argument("expected a positive integer");
    auto n = std::stoull(value); if (n < 1 || n > maximum) throw std::invalid_argument("integer outside benchmark limit"); return static_cast<std::size_t>(n);
}
std::vector<MarketData> generate(std::size_t count) {
    std::vector<MarketData> rows; rows.reserve(count);
    int year = 2024, month = 1, day = 2, weekday = 1;
    double price = 100;
    while (rows.size() < count) {
        if (weekday < 5) {
            for (int minute = 570; minute < 960 && rows.size() < count; minute += 2) {
                std::ostringstream timestamp;
                timestamp << year << '-' << std::setfill('0') << std::setw(2) << month << '-' << std::setw(2) << day
                          << 'T' << std::setw(2) << minute / 60 << ':' << std::setw(2) << minute % 60 << ":00";
                MarketData bar; bar.timestamp = timestamp.str(); bar.open = price;
                price += .001 + .015 * std::sin(static_cast<double>(rows.size()) * .013);
                bar.close = price; bar.low = std::min(bar.open, price) - .02;
                bar.high = std::max(bar.open, price) + .02; bar.volume = 1000;
                rows.push_back(bar);
            }
        }
        const int lengths[] = {31,28,31,30,31,30,31,31,30,31,30,31};
        const bool leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        const int length = lengths[month - 1] + (month == 2 && leap ? 1 : 0);
        if (++day > length) { day = 1; if (++month > 12) { month = 1; ++year; } }
        weekday = (weekday + 1) % 7;
    }
    return rows;
}
double naive_sma(const std::vector<double>& prices, std::size_t period) {
    double checksum = 0;
    for (std::size_t i = period - 1; i < prices.size(); ++i) {
        double sum = 0;
        for (std::size_t j = i + 1 - period; j <= i; ++j) sum += prices[j];
        checksum += sum / period;
    }
    return checksum;
}
double rolling_sma(const std::vector<double>& prices, std::size_t period) {
    std::deque<double> window; double sum = 0, checksum = 0;
    for (const double price : prices) {
        window.push_back(price); sum += price;
        if (window.size() > period) { sum -= window.front(); window.pop_front(); }
        if (window.size() == period) checksum += sum / period;
    }
    return checksum;
}
const char* compiler_name() {
#if defined(__clang__)
    return "Clang " __clang_version__;
#elif defined(__GNUC__)
    return "GCC " __VERSION__;
#elif defined(_MSC_VER)
    return "MSVC";
#else
    return "unknown";
#endif
}
}
int main(int argc, char** argv) {
    try {
        std::size_t bars = 39000, repetitions = 5;
        std::string name = "SMA_CROSSOVER";
        for (int i = 1; i < argc; ++i) {
            const std::string argument = argv[i];
            if (i + 1 >= argc) throw std::invalid_argument("missing benchmark argument value");
            const std::string value = argv[++i];
            if (argument == "--bars") bars = positive_integer(value, 2000000);
            else if (argument == "--repetitions") repetitions = positive_integer(value, 30);
            else if (argument == "--strategy") name = canonical_strategy(value);
            else throw std::invalid_argument("unknown benchmark argument");
        }
        if (bars < 200) throw std::invalid_argument("at least 200 bars required");
        const auto records = generate(bars);
        auto parser = std::make_shared<CSVParser>();
        if (!parser->load_records(records)) throw std::runtime_error(parser->get_last_error());
        auto strategy = make_strategy(name);
        auto risk = std::make_shared<RiskManager>();
        if (!risk->initialize(RiskParameters{})) throw std::runtime_error("benchmark risk configuration rejected");
        Backtester engine;
        if (!engine.initialize(BacktestConfig{})) throw std::runtime_error("benchmark configuration rejected");
        (void)engine.run_backtest(strategy, parser, risk); // Excluded warmup.
        std::vector<double> core_times;
        std::size_t events = 0, fills = 0;
        Cents expected_equity = 0;
        for (std::size_t i = 0; i < repetitions; ++i) {
            auto start = Clock::now();
            auto result = engine.run_backtest(strategy, parser, risk);
            core_times.push_back(elapsed(start));
            if (i && result.final_equity_cents != expected_equity) throw std::runtime_error("benchmark replay was not deterministic");
            expected_equity = result.final_equity_cents; events = result.events.size(); fills = result.trades.size();
        }
        std::vector<double> prices; prices.reserve(records.size());
        for (const auto& record : records) prices.push_back(record.close);
        (void)naive_sma(prices, 200); (void)rolling_sma(prices, 200);
        std::vector<double> naive_times, rolling_times;
        double naive_checksum = 0, rolling_checksum = 0;
        for (std::size_t i = 0; i < repetitions; ++i) {
            // Alternate ordering to reduce systematic cache/order bias.
            auto measure_naive = [&] { auto start = Clock::now(); naive_checksum = naive_sma(prices, 200); naive_times.push_back(elapsed(start)); };
            auto measure_rolling = [&] { auto start = Clock::now(); rolling_checksum = rolling_sma(prices, 200); rolling_times.push_back(elapsed(start)); };
            if (i % 2) { measure_rolling(); measure_naive(); } else { measure_naive(); measure_rolling(); }
        }
        const double difference = std::abs(naive_checksum - rolling_checksum);
        if (difference > 1e-8 * std::max(1.0, std::abs(naive_checksum))) throw std::runtime_error("SMA checksums disagree");
        Json report = {{"benchmark", "synthetic in-memory engine and equivalent SMA kernels"}, {"strategy", name},
            {"bars", bars}, {"repetitions", repetitions}, {"warmup_iterations_excluded", 1}, {"compiler", compiler_name()},
            {"cxx_standard", 17}, {"clock", "std::chrono::steady_clock"},
#ifdef NDEBUG
            {"ndebug", true},
#else
            {"ndebug", false},
#endif
            {"synthetic_fixture", "deterministic fictional 2-minute weekday sessions; no exchange holiday calendar"},
            {"core", {{"milliseconds", core_times}, {"median_ms", median(core_times)}, {"bars_per_second_at_median", bars * 1000.0 / median(core_times)},
                {"includes", "strategy reset, in-memory validation, strategy/risk/ledger loop, statistics, result collection and return copy"},
                {"excludes", "bar generation, initial parser loading, process startup, CSV I/O, configuration-file I/O, JSON serialization, file writes"},
                {"event_count", events}, {"fill_count", fills}, {"replay_equity_cents", expected_equity}}},
            {"sma_kernel", {{"period", 200}, {"naive_ms", naive_times}, {"rolling_ms", rolling_times},
                {"naive_median_ms", median(naive_times)}, {"rolling_median_ms", median(rolling_times)},
                {"naive_checksum", naive_checksum}, {"rolling_checksum", rolling_checksum}, {"absolute_checksum_difference", difference},
                {"scope", "equivalent arithmetic-only SMA200 outputs; not whole strategy or historical engine versions"}}},
            {"interpretation", "local synthetic measurement only; no market profitability or production throughput claim"}};
        std::cout << report.dump(2) << '\n'; return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
