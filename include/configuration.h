#pragma once
#include "backtester/backtester.h"
#include "nlohmann/json.hpp"
#include <memory>
namespace TradingBot {
using Json = nlohmann::json;
struct Configuration {
    BacktestConfig backtest;
    RiskParameters risk;
    std::map<std::string, std::map<std::string, double>> strategies;
    Json effective;
};
std::shared_ptr<Strategy> make_strategy(const std::string& name);
std::string canonical_strategy(const std::string& name);
Configuration parse_configuration(const Json& input);
Configuration read_configuration(const std::string& filename);
Json results_to_json(const BacktestResults& results);
}
