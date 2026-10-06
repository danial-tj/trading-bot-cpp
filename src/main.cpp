#include "trading_bot.h"
#include <iostream>
#include <set>
int main(int argc, char* argv[]) {
    try {
        std::string config, data, strategy = "SMA_CROSSOVER", output;
        std::set<std::string> seen;
        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            if (arg == "--help") {
                std::cout << "Offline Trading Bot 2.0\nUsage: trading_bot --data FILE [--config FILE] [--strategy NAME] [--output FILE]\n"
                    << "Strategies: SMA_CROSSOVER, EMA_CROSSOVER, RSI, VWAP_OPENING\n"
                    << "With no --output, writes JSON to stdout. No broker or live execution support.\n";
                return 0;
            }
            if (arg == "--version") { std::cout << "2.0.0\n"; return 0; }
            if (arg != "--config" && arg != "--data" && arg != "--strategy" && arg != "--output")
                throw std::invalid_argument("Unknown argument: " + arg);
            if (!seen.insert(arg).second || i + 1 >= argc) throw std::invalid_argument("Duplicate or missing value for " + arg);
            std::string value = argv[++i];
            if (value.empty() || value.rfind("--", 0) == 0) throw std::invalid_argument("Missing value for " + arg);
            if (arg == "--config") config = value;
            else if (arg == "--data") data = value;
            else if (arg == "--strategy") strategy = value;
            else output = value;
        }
        if (data.empty()) throw std::invalid_argument("--data is required. Use --help for usage.");
        TradingBot::TradingBot bot;
        if (!bot.initialize(config) || !bot.run_backtest(data, strategy)) throw std::runtime_error(bot.get_last_error());
        if (output.empty()) std::cout << bot.get_report().dump(2) << '\n';
        else bot.generate_report(output);
        return 0;
    } catch (const std::exception& e) {
        std::cerr << TradingBot::Json{{"error", e.what()}}.dump() << '\n';
        return 1;
    }
}
