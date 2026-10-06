#include "trading_bot.h"
#include <fstream>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#ifndef TRADING_BOT_COMMIT
#define TRADING_BOT_COMMIT "unknown"
#endif
#ifndef TRADING_BOT_SOURCE_SHA256
#define TRADING_BOT_SOURCE_SHA256 "unknown"
#endif
namespace TradingBot {
namespace {
std::string fingerprint(const std::string& filename) {
    std::ifstream input(filename, std::ios::binary);
    if (!input) throw std::runtime_error("Cannot fingerprint dataset");
    std::uint64_t hash = 14695981039346656037ULL;
    char byte;
    while (input.get(byte)) { hash ^= static_cast<unsigned char>(byte); hash *= 1099511628211ULL; }
    if (!input.eof()) throw std::runtime_error("Failed to read dataset for fingerprint");
    std::ostringstream out; out << std::hex << std::setw(16) << std::setfill('0') << hash; return out.str();
}
Json benchmark(const CSVParser& parser, const BacktestConfig& config) {
    Ledger ledger(config.initial_capital);
    std::vector<double> curve{from_cents(ledger.cash_cents())};
    bool started = false;
    double last = 0;
    for (size_t i = 0; i < parser.get_data_count(); ++i) {
        const auto& bar = parser.get_data(i);
        const auto date = bar.timestamp.substr(0, 10);
        if ((!config.start_date.empty() && date < config.start_date) || (!config.end_date.empty() && date > config.end_date)) continue;
        if (!started) {
            started = true;
            const double price = from_cents(to_cents(bar.open * (1 + config.slippage)));
            if (price <= 0) throw std::invalid_argument("Buy-and-hold opening price rounds to zero cents");
            std::int64_t low = 0, high = ledger.cash_cents() / to_cents(price);
            while (low < high) {
                const auto candidate = low + (high - low + 1) / 2;
                const auto gross = checked_multiply(to_cents(price), candidate);
                const auto fee = to_cents(from_cents(gross) * config.commission_rate + config.commission_fixed);
                if (checked_add(gross, fee) <= ledger.cash_cents()) low = candidate;
                else high = candidate - 1;
            }
            if (low > 0) {
                const auto gross = checked_multiply(to_cents(price), low);
                ledger.buy_cents(low, to_cents(price), to_cents(from_cents(gross) * config.commission_rate + config.commission_fixed), bar.timestamp);
            }
        }
        last = bar.close;
        curve.push_back(from_cents(ledger.equity_cents(last)));
    }
    if (!started) return nullptr;
    return {{"name","Buy and hold"}, {"equity_curve",curve}, {"final_equity",curve.back()},
        {"total_return",curve.back() / curve.front() - 1}, {"quantity",ledger.quantity()},
        {"fees",from_cents(ledger.fees_cents())},
        {"assumption","Precommitted purchase at first in-range open, maximum affordable whole shares, same entry fees/slippage, hold through final close. No allocation cap or exit fee; exposure differs from strategy."}};
}
}
bool TradingBot::initialize(const std::string& config_file) {
    initialized_ = false;
    error_.clear(); report_ = nullptr;
    try { config_ = read_configuration(config_file); initialized_ = true; return true; }
    catch (const std::exception& e) { error_ = e.what(); return false; }
}
bool TradingBot::run_backtest(const std::string& data_file, const std::string& strategy_name) {
    error_.clear(); report_ = nullptr; results_ = BacktestResults{};
    try {
        if (!initialized_) throw std::logic_error("Initialize the simulator before running");
        const auto input_fingerprint = fingerprint(data_file);
        auto parser = std::make_shared<CSVParser>();
        if (!parser->load_data(data_file)) throw std::invalid_argument(parser->get_last_error());
        const auto name = canonical_strategy(strategy_name);
        auto strategy = make_strategy(name);
        if (!strategy->initialize(config_.strategies.at(name))) throw std::invalid_argument("Strategy initialization failed");
        auto risk = std::make_shared<RiskManager>();
        if (!risk->initialize(config_.risk)) throw std::invalid_argument("Risk initialization failed");
        Backtester engine;
        if (!engine.initialize(config_.backtest)) throw std::invalid_argument("Backtest initialization failed");
        results_ = engine.run_backtest(strategy, parser, risk);
        if (fingerprint(data_file) != input_fingerprint) throw std::runtime_error("Dataset changed during the run; retry with an immutable input");
        report_ = {{"schema_version",1}, {"engine_version","2.0.0"}, {"engine_commit",TRADING_BOT_COMMIT},
            {"engine_source_sha256",TRADING_BOT_SOURCE_SHA256},
            {"dataset_fingerprint",{{"algorithm","fnv1a64"},{"value",input_fingerprint}}},
            {"strategy",name}, {"effective_config",config_.effective}, {"bar_count",parser->get_data_count()},
            {"assumptions", {"Offline simulation; one asset, one nominal currency; whole shares; no borrowing.",
                "Signals use completed bars; eligible orders fill at the next observed open with slippage and fees.",
                "Stops and take-profit use completed closes; gaps can exceed their thresholds.",
                "Open holdings remain marked at the final close; no fabricated liquidation.",
                "Cash, fees, prices and cost basis use checked signed 64-bit cents; indicators use floating point.",
                "Amounts round half away from zero. Entry fees are included in cost basis; partial exits allocate basis proportionally in cents.",
                "The next quoted open is assumed executable; no liquidity or volume participation model. Later execution-bar HLCV is never used to qualify its opening fill.",
                "Intraday timestamps denote bar opens in exchange-local time; CSV must contain correctly normalized regular-session history.",
                "Bearish VWAP signals are research only; short execution is disabled."}},
            {"results", results_to_json(results_)}, {"benchmark",benchmark(*parser,config_.backtest)}};
        return true;
    } catch (const std::exception& e) { error_ = e.what(); return false; }
}
void TradingBot::generate_report(const std::string& output_file) const {
    if (report_.is_null()) throw std::logic_error("No successful result to save");
    std::ofstream out(output_file, std::ios::binary);
    if (!out || !(out << report_.dump(2) << '\n')) throw std::runtime_error("Cannot write report: " + output_file);
}
bool TradingBot::run_backtest_with_api(const std::string&, const std::string&, const std::string&, const std::string&, DataInterval) {
    error_ = "Network adapters are disabled in the offline simulator; provide a validated CSV."; return false;
}
bool TradingBot::fetch_market_data(const std::string&, const std::string&, const std::string&, const std::string&, DataInterval) {
    error_ = "Network adapters are disabled in the offline simulator."; return false;
}
bool TradingBot::set_api_provider(APIProvider) { error_ = "Network adapters are disabled."; return false; }
}
