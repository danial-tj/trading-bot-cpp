#include "configuration.h"
#include "trading_bot.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
using namespace TradingBot;
int checks = 0;
void check(bool condition, const std::string& message) {
    ++checks; if (!condition) throw std::runtime_error(message);
}
template<class F> void rejects(F action, const std::string& message) {
    bool failed = false; try { action(); } catch (const std::exception&) { failed = true; }
    check(failed, message);
}
int main() {
    try {
        auto config = parse_configuration(Json::object());
        check(config.backtest.initial_capital == 10000, "default capital");
        check(config.strategies.at("VWAP_OPENING").at("opening_window_minutes") == 30, "opening window");
        check(parse_configuration(config.effective).effective == config.effective, "effective configuration round trip");
        auto custom = parse_configuration({{"backtesting",{{"initial_capital",500},{"commission_fixed",1},{"slippage",.01}}},
            {"strategies",{{"SMA_CROSSOVER",{{"short_period",3},{"long_period",9}}}}}});
        check(custom.backtest.initial_capital == 500 && custom.backtest.commission_fixed == 1 && custom.backtest.slippage == .01, "cost overrides");
        check(custom.strategies.at("SMA_CROSSOVER").at("long_period") == 9, "strategy overrides");
        rejects([]{ parse_configuration({{"unknown",1}}); }, "unknown section rejected");
        rejects([]{ parse_configuration({{"backtesting",{{"initial_capitla",100}}}}); }, "typo rejected");
        rejects([]{ parse_configuration({{"backtesting",{{"initial_capital",true}}}}); }, "boolean number rejected");
        rejects([]{ parse_configuration({{"backtesting",{{"initial_capital",-1}}}}); }, "negative capital rejected");
        rejects([]{ parse_configuration({{"backtesting",{{"slippage",1}}}}); }, "invalid cost rejected");
        rejects([]{ parse_configuration({{"backtesting",{{"enable_short_selling",true}}}}); }, "short execution blocked");
        rejects([]{ parse_configuration({{"backtesting",{{"start_date","2024-02-30"}}}}); }, "calendar validation");
        rejects([]{ parse_configuration({{"backtesting",{{"start_date","2025-01-01"},{"end_date","2024-01-01"}}}}); }, "range validation");
        rejects([]{ parse_configuration({{"risk_management",{{"position_sizing_atr",2}}}}); }, "unsupported ATR rejected");
        rejects([]{ parse_configuration({{"risk_management",{{"max_daily_loss",0}}}}); }, "zero daily risk rejected");
        rejects([]{ parse_configuration({{"strategies",{{"RSI",{{"rsi_period",2.5}}}}}}); }, "fractional period rejected");
        rejects([]{ parse_configuration({{"strategies",{{"SMA_CROSSOVER",{{"short_period",30},{"long_period",10}}}}}}); }, "inverted periods rejected");
        rejects([]{ parse_configuration({{"strategies",{{"VWAP_OPENING",{{"window_minutes",15}}}}}}); }, "misspelled VWAP parameter rejected");
        rejects([]{ make_strategy("BOGUS"); }, "unknown strategy rejected");
        rejects([]{ read_configuration("this-file-does-not-exist.json"); }, "missing explicit file rejected");
        auto duplicate = std::filesystem::temp_directory_path() / "trading_bot_duplicate_keys_test.json";
        { std::ofstream f(duplicate); f << "{\"backtesting\":{\"initial_capital\":10,\"initial_capital\":100}}"; }
        rejects([&]{ read_configuration(duplicate.string()); }, "duplicate key rejected");
        std::filesystem::remove(duplicate);
        ::TradingBot::TradingBot bot;
        check(!bot.run_backtest("data/daily_demo.csv","SMA"), "uninitialized run fails safely");
        check(bot.initialize(), "orchestrator initializes");
        check(bot.run_backtest("data/daily_demo.csv","SMA"), bot.get_last_error());
        const auto first = bot.get_report();
        check(bot.run_backtest("data/daily_demo.csv","RSI"), bot.get_last_error());
        check(bot.run_backtest("data/daily_demo.csv","SMA"), bot.get_last_error());
        check(first == bot.get_report(), "runs reset all orchestration state");
        check(!bot.initialize("missing.json"), "failed reinitialize reports failure");
        check(!bot.run_backtest("data/daily_demo.csv","SMA"), "failed initialization cannot reuse stale config");
        std::cout << checks << " configuration/integration checks passed\n"; return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
