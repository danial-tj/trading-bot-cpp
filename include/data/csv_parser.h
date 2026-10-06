#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace TradingBot {
struct MarketData {
    // Intraday bars are stamped with their start in exchange-local time.
    std::string timestamp;
    double open = 0, high = 0, low = 0, close = 0, volume = 0;
    std::string session_date;
    int minute_of_day = -1;
    bool is_intraday = false;
    // Calendar seconds for ordering only, NOT a UTC timestamp.
    std::int64_t epoch_seconds = 0;
};
class CSVParser {
public:
    bool load_data(const std::string& filename);
    bool load_records(const std::vector<MarketData>& records);
    const MarketData& get_data(size_t index) const;
    size_t get_data_count() const;
    std::vector<MarketData> get_data_range(size_t start, size_t end) const;
    bool validate_data() const;
    void clear();
    const std::string& get_last_error() const { return last_error_; }
    static bool valid_timestamp(const std::string& timestamp);
private:
    std::vector<MarketData> data_;
    std::string last_error_;
};
}
