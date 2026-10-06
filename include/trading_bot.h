#pragma once
#include "configuration.h"
#include "data/api_data_fetcher.h"
namespace TradingBot {
// Each offline run owns fresh strategy, risk and parser state.
class TradingBot {
public:
    bool initialize(const std::string& config_file = "");
    bool run_backtest(const std::string& data_file, const std::string& strategy_name);
    void generate_report(const std::string& output_file) const;
    const BacktestResults& get_results() const { return results_; }
    const Json& get_report() const { return report_; }
    const std::string& get_last_error() const { return error_; }
    bool run_backtest_with_api(const std::string&, const std::string&, const std::string&, const std::string&, DataInterval = DataInterval::DAILY);
    bool fetch_market_data(const std::string&, const std::string&, const std::string&, const std::string&, DataInterval = DataInterval::DAILY);
    bool set_api_provider(APIProvider);
private:
    Configuration config_;
    BacktestResults results_;
    Json report_;
    std::string error_;
    bool initialized_ = false;
};
}
