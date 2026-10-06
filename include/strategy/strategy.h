#pragma once

#include "data/csv_parser.h"
#include <deque>
#include <map>
#include <string>
#include <vector>

namespace TradingBot {

enum class SignalType { BUY, SELL, HOLD, SHORT, COVER };

struct TradingSignal {
    SignalType type = SignalType::HOLD;
    double price = 0.0;
    double quantity = 0.0;
    std::string timestamp;
    std::string reason;
    // Exclusive latest allowed fill timestamp. Empty means no strategy expiry.
    std::string valid_until;
};

struct Position {
    double quantity = 0.0;
    double avg_price = 0.0;
    std::string symbol;
};

// Seed with the arithmetic mean of the first period observations, then recurse.
struct StreamingEMA {
    int period = 1;
    std::size_t count = 0;
    double value = 0.0;
    double seed_sum = 0.0;
    void reset(int new_period);
    void push(double close);
    bool ready() const;
};

class Strategy {
public:
    explicit Strategy(const std::string& name);
    virtual ~Strategy();
    const std::string& get_name() const;
    // Successful initialize also resets every observation and indicator.
    virtual bool initialize(const std::map<std::string, double>& params) = 0;
    virtual TradingSignal generate_signal(const MarketData&, const Position&) = 0;
    virtual void update(const MarketData& data);
    virtual std::map<std::string, double> get_parameters() const = 0;
    virtual bool validate_parameters(const std::map<std::string, double>&) const = 0;
protected:
    std::string name_;
    std::map<std::string, double> parameters_;
    double calculate_sma(const std::vector<MarketData>&, int period);
    double calculate_ema(const std::vector<MarketData>&, int period);
    double calculate_rsi(const std::vector<MarketData>&, int period);
};

class SMACrossoverStrategy : public Strategy {
public:
    SMACrossoverStrategy();
    bool initialize(const std::map<std::string, double>&) override;
    TradingSignal generate_signal(const MarketData&, const Position&) override;
    std::map<std::string, double> get_parameters() const override;
    bool validate_parameters(const std::map<std::string, double>&) const override;
private:
    int short_period_ = 10, long_period_ = 20;
    std::deque<double> short_prices_, long_prices_;
    double short_sum_ = 0.0, long_sum_ = 0.0, previous_difference_ = 0.0;
    bool previous_ready_ = false;
};

class EMAStrategy : public Strategy {
public:
    EMAStrategy();
    bool initialize(const std::map<std::string, double>&) override;
    TradingSignal generate_signal(const MarketData&, const Position&) override;
    std::map<std::string, double> get_parameters() const override;
    bool validate_parameters(const std::map<std::string, double>&) const override;
private:
    StreamingEMA short_ema_, long_ema_;
    double previous_difference_ = 0.0;
    bool previous_ready_ = false;
};

class RSIStrategy : public Strategy {
public:
    RSIStrategy();
    bool initialize(const std::map<std::string, double>&) override;
    TradingSignal generate_signal(const MarketData&, const Position&) override;
    std::map<std::string, double> get_parameters() const override;
    bool validate_parameters(const std::map<std::string, double>&) const override;
private:
    int rsi_period_ = 14;
    double oversold_threshold_ = 30.0, overbought_threshold_ = 70.0;
    std::size_t changes_ = 0;
    double previous_close_ = 0.0, average_gain_ = 0.0, average_loss_ = 0.0;
    double previous_rsi_ = 50.0;
    bool have_close_ = false, previous_ready_ = false;
};

struct VWAPDiagnostics {
    std::string session_date;
    std::string reason;
    double session_vwap = 0.0;
    double previous_vwap = 0.0;
    double fast_ema = 0.0, medium_ema = 0.0, slow_ema = 0.0;
    int weekly_direction = 0, monthly_direction = 0;
    std::size_t weekly_completed_bars = 0, monthly_completed_bars = 0;
    std::size_t intraday_bars = 0, session_bars = 0;
    bool intraday_ready = false, trend_ready = false;
};

class VWAPOpeningStrategy : public Strategy {
public:
    VWAPOpeningStrategy();
    bool initialize(const std::map<std::string, double>&) override;
    TradingSignal generate_signal(const MarketData&, const Position&) override;
    std::map<std::string, double> get_parameters() const override;
    bool validate_parameters(const std::map<std::string, double>&) const override;
    const VWAPDiagnostics& diagnostics() const { return diagnostics_; }
private:
    struct CompletedTrend {
        StreamingEMA ema;
        long long current_key = 0;
        double latest_close = 0.0;
        bool have_group = false, first_group = true, ready = false;
        bool current_group_complete = true;
        int direction = 0;
        std::size_t completed = 0;
        void reset(int period);
        void push(long long key, double close);
    };
    StreamingEMA fast_, medium_, slow_;
    CompletedTrend weekly_, monthly_;
    VWAPDiagnostics diagnostics_;
    std::string last_timestamp_;
    long double cumulative_pv_ = 0.0L, cumulative_volume_ = 0.0L;
    bool entry_attempted_ = false;
    bool session_complete_ = false;
    int last_session_minute_ = -1;
};

} // namespace TradingBot
