#include "configuration.h"
#include <cmath>
#include <fstream>
#include <set>
#include <stdexcept>
namespace TradingBot {
namespace {
void object_keys(const Json& value, const std::set<std::string>& allowed, const std::string& section) {
    if (!value.is_object()) throw std::invalid_argument(section + " must be an object");
    for (const auto& item : value.items())
        if (!allowed.count(item.key())) throw std::invalid_argument("Unsupported setting: " + section + "." + item.key());
}
double number(const Json& input, const std::string& key, double fallback, double low, double high) {
    if (!input.contains(key)) return fallback;
    if (!input.at(key).is_number()) throw std::invalid_argument(key + " must be a number");
    double value = input.at(key).get<double>();
    if (!std::isfinite(value) || value < low || value > high)
        throw std::invalid_argument(key + " is outside the supported range");
    return value;
}
std::string string_value(const Json& input, const std::string& key, const std::string& fallback) {
    if (!input.contains(key)) return fallback;
    if (!input.at(key).is_string()) throw std::invalid_argument(key + " must be a string");
    return input.at(key).get<std::string>();
}
bool date_valid(const std::string& value) {
    if (value.empty()) return true;
    if (value.size() != 10 || value[4] != '-' || value[7] != '-') return false;
    for (size_t i = 0; i < value.size(); ++i)
        if (i != 4 && i != 7 && (value[i] < '0' || value[i] > '9')) return false;
    const int y = std::stoi(value.substr(0,4)), m = std::stoi(value.substr(5,2)), d = std::stoi(value.substr(8,2));
    const int days[] = {0,31,28,31,30,31,30,31,31,30,31,30,31};
    return y >= 1900 && y <= 9999 && m >= 1 && m <= 12 && d >= 1 &&
        d <= days[m] + (m == 2 && y % 4 == 0 && (y % 100 != 0 || y % 400 == 0));
}
}
std::string canonical_strategy(const std::string& name) {
    if (name == "SMA") return "SMA_CROSSOVER";
    if (name == "EMA") return "EMA_CROSSOVER";
    if (name == "RSI_STRATEGY") return "RSI";
    return name;
}
std::shared_ptr<Strategy> make_strategy(const std::string& name) {
    const auto key = canonical_strategy(name);
    if (key == "SMA_CROSSOVER") return std::make_shared<SMACrossoverStrategy>();
    if (key == "EMA_CROSSOVER") return std::make_shared<EMAStrategy>();
    if (key == "RSI") return std::make_shared<RSIStrategy>();
    if (key == "VWAP_OPENING") return std::make_shared<VWAPOpeningStrategy>();
    throw std::invalid_argument("Unknown strategy: " + name);
}
Configuration parse_configuration(const Json& input) {
    object_keys(input, {"backtesting", "risk_management", "strategies"}, "configuration");
    Configuration c;
    auto b = input.value("backtesting", Json::object());
    object_keys(b, {"initial_capital", "commission_rate", "commission_fixed", "slippage", "start_date", "end_date", "enable_short_selling", "symbol"}, "backtesting");
    c.backtest.initial_capital = number(b, "initial_capital", 10000, 0.01, 1e9);
    c.backtest.commission_rate = number(b, "commission_rate", .001, 0, .1);
    c.backtest.commission_fixed = number(b, "commission_fixed", 0, 0, 1e6);
    c.backtest.slippage = number(b, "slippage", .0001, 0, .1);
    c.backtest.start_date = string_value(b, "start_date", "");
    c.backtest.end_date = string_value(b, "end_date", "");
    c.backtest.symbol = string_value(b, "symbol", "SIM");
    if (!date_valid(c.backtest.start_date) || !date_valid(c.backtest.end_date) ||
        (!c.backtest.start_date.empty() && !c.backtest.end_date.empty() && c.backtest.start_date > c.backtest.end_date))
        throw std::invalid_argument("Dates must be valid YYYY-MM-DD with start_date <= end_date");
    if (c.backtest.symbol.empty() || c.backtest.symbol.size() > 24 ||
        c.backtest.symbol.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-_") != std::string::npos)
        throw std::invalid_argument("symbol must be 1-24 letters, digits, periods, hyphens or underscores");
    if (b.contains("enable_short_selling") && (!b.at("enable_short_selling").is_boolean() || b.at("enable_short_selling").get<bool>()))
        throw std::invalid_argument("Short execution is unsupported; enable_short_selling must be false. Bearish VWAP signals are recorded for research.");
    c.backtest.enable_short_selling = false;
    auto r = input.value("risk_management", Json::object());
    object_keys(r, {"max_position_size", "max_drawdown", "stop_loss_pct", "take_profit_pct", "max_daily_loss"}, "risk_management");
    c.risk.max_position_size = number(r, "max_position_size", .1, .000001, 1);
    c.risk.max_drawdown = number(r, "max_drawdown", .2, .000001, 1);
    c.risk.stop_loss_pct = number(r, "stop_loss_pct", .05, 0, 1);
    c.risk.take_profit_pct = number(r, "take_profit_pct", .1, 0, 1);
    c.risk.max_daily_loss = number(r, "max_daily_loss", .05, .000001, 1);
    auto s = input.value("strategies", Json::object());
    object_keys(s, {"SMA_CROSSOVER", "EMA_CROSSOVER", "RSI", "VWAP_OPENING"}, "strategies");
    for (const std::string name : {"SMA_CROSSOVER", "EMA_CROSSOVER", "RSI", "VWAP_OPENING"}) {
        auto strategy = make_strategy(name);
        auto params = strategy->get_parameters();
        const auto overrides = s.value(name, Json::object());
        std::set<std::string> keys;
        for (const auto& p : params) keys.insert(p.first);
        object_keys(overrides, keys, "strategies." + name);
        for (auto& p : params) p.second = number(overrides, p.first, p.second, -1e8, 1e8);
        if (!strategy->validate_parameters(params) || !strategy->initialize(params))
            throw std::invalid_argument("Invalid parameters for " + name);
        c.strategies[name] = strategy->get_parameters();
    }
    c.effective = {{"backtesting", {{"initial_capital", c.backtest.initial_capital}, {"commission_rate", c.backtest.commission_rate},
        {"commission_fixed", c.backtest.commission_fixed}, {"slippage", c.backtest.slippage}, {"start_date", c.backtest.start_date},
        {"end_date", c.backtest.end_date}, {"enable_short_selling", false}, {"symbol", c.backtest.symbol}}},
        {"risk_management", {{"max_position_size", c.risk.max_position_size}, {"max_drawdown", c.risk.max_drawdown},
        {"stop_loss_pct", c.risk.stop_loss_pct}, {"take_profit_pct", c.risk.take_profit_pct}, {"max_daily_loss", c.risk.max_daily_loss}}},
        {"strategies", c.strategies}};
    return c;
}
Configuration read_configuration(const std::string& filename) {
    if (filename.empty()) return parse_configuration(Json::object());
    std::ifstream file(filename);
    if (!file) throw std::invalid_argument("Cannot open configuration: " + filename);
    std::vector<std::set<std::string>> objects;
    auto reject_duplicates = [&objects](int, Json::parse_event_t event, Json& parsed) {
        if (event == Json::parse_event_t::object_start) objects.emplace_back();
        else if (event == Json::parse_event_t::object_end) objects.pop_back();
        else if (event == Json::parse_event_t::key && !objects.back().insert(parsed.get<std::string>()).second)
            throw std::invalid_argument("Duplicate JSON key: " + parsed.get<std::string>());
        return true;
    };
    return parse_configuration(Json::parse(file, reject_duplicates));
}
}
