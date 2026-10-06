> Historical pre-2.0 document. Its feature, build, API, test and performance claims are superseded by [the current README](README.md) and [verified progress](docs/PROGRESS.md). Network adapters are excluded from the offline release.

# Quick Start: API Integration

## 🎉 Congratulations! Your Trading Bot Now Supports Live Data!

You can now fetch real market data from external APIs and run backtests immediately.

## 🚀 Run Your First API-Powered Backtest (3 Steps)

### Step 1: Test the API Fetcher

```bash
cd build\bin\Release
.\test_api_data_fetcher.exe
```

This will:
- ✅ Fetch real Apple (AAPL) data from Yahoo Finance
- ✅ Save data to CSV files
- ✅ Test Alpha Vantage API (with demo key)
- ✅ Download data for multiple stocks (AAPL, MSFT, GOOGL, TSLA, AMZN)

### Step 2: Run Full Integration Test

```bash
.\test_trading_bot_with_api.exe
```

This will:
- ✅ Fetch real market data via API
- ✅ Run backtests with SMA, EMA, and RSI strategies
- ✅ Generate HTML reports
- ✅ Compare strategies across multiple symbols

### Step 3: Build Your Own Bot

Create a new file `my_bot.cpp`:

```cpp
#include "trading_bot.h"
#include <iostream>

int main() {
    TradingBot::TradingBot bot;
    bot.initialize("config.json");
    
    // Backtest Apple stock with SMA strategy
    bot.run_backtest_with_api(
        "AAPL",           // Symbol
        "SMA_CROSSOVER",  // Strategy
        "2024-01-01",     // Start date
        "2024-10-07",     // End date
        TradingBot::DataInterval::DAILY
    );
    
    // Get results
    auto results = bot.get_results();
    std::cout << "Total Return: " << (results.total_return * 100) << "%" << std::endl;
    std::cout << "Total Trades: " << results.total_trades << std::endl;
    std::cout << "Win Rate: " << (results.win_rate * 100) << "%" << std::endl;
    
    // Generate report
    bot.generate_report("AAPL_backtest_report.html");
    
    return 0;
}
```

## 📊 Supported Data Sources

### Yahoo Finance (Default - No Setup Required)
- **No API key needed** ✨
- Works immediately out of the box
- Supports stocks, ETFs, and crypto
- Examples: `AAPL`, `MSFT`, `SPY`, `BTC-USD`

### Alpha Vantage (Optional - For Intraday Data)
1. Get FREE API key: https://www.alphavantage.co/support/#api-key
2. Update `config.json`:
   ```json
   {
       "api": {
           "alpha_vantage": {
               "api_key": "YOUR_KEY_HERE"
           }
       }
   }
   ```
3. Now you can fetch 1-minute, 5-minute, 15-minute data!

## 💡 Common Use Cases

### Download Data for Later Use
```cpp
bot.fetch_market_data(
    "TSLA",                       // Symbol  
    "2023-01-01",                 // Start
    "2024-10-07",                 // End
    "data/TSLA_historical.csv"    // Save to file
);
```

### Test Multiple Strategies
```cpp
std::vector<std::string> strategies = {"SMA_CROSSOVER", "EMA_CROSSOVER", "RSI"};

for (const auto& strategy : strategies) {
    bot.run_backtest_with_api("AAPL", strategy, "2024-01-01", "2024-10-07");
    auto results = bot.get_results();
    std::cout << strategy << " Return: " << (results.total_return * 100) << "%" << std::endl;
}
```

### Compare Multiple Stocks
```cpp
std::vector<std::string> symbols = {"AAPL", "MSFT", "GOOGL", "AMZN"};

for (const auto& symbol : symbols) {
    bot.run_backtest_with_api(symbol, "SMA_CROSSOVER", "2024-01-01", "2024-10-07");
    auto results = bot.get_results();
    std::cout << symbol << ": " << (results.total_return * 100) << "%" << std::endl;
}
```

## 🎯 Next Steps

1. **Try it now**: Run `test_api_data_fetcher.exe`
2. **Test strategies**: Run `test_trading_bot_with_api.exe`
3. **Read docs**: Check `API_INTEGRATION_GUIDE.md` for detailed documentation
4. **Build your own**: Create custom strategies with real data
5. **Get API key**: Optional - for intraday and more detailed data

## 🐛 Troubleshooting

**"Failed to fetch data"**
- Check your internet connection
- Verify the symbol exists (e.g., AAPL not APPLE)
- Try Yahoo Finance first (works without API key)

**"API rate limit exceeded"**
- Alpha Vantage free tier: 25 requests/day
- Enable caching to reduce API calls
- Use Yahoo Finance for unlimited requests

**"Symbol not found"**
- Check spelling: `AAPL` not `APPLE`
- For crypto use format: `BTC-USD`, `ETH-USD`
- Verify symbol on finance.yahoo.com

## 📁 Files Created

After running the tests, you'll find:
- `*.csv` - Downloaded market data files
- `*_report.html` - Backtest result reports  
- `temp_*_data.csv` - Temporary API data files

## 🔥 What Makes This Cool?

- **No manual data downloads** - Get data with one line of code
- **Always up-to-date** - Fetch latest market data instantly
- **Multiple sources** - Yahoo Finance and Alpha Vantage
- **Built-in caching** - Don't waste API calls
- **Automatic CSV export** - Save data for offline use
- **Seamless integration** - Works with existing backtest system

## 📚 More Information

- **Full API Guide**: `API_INTEGRATION_GUIDE.md`
- **Development Guide**: `DEVELOPMENT.md`
- **Main README**: `README.md`

## 🎊 You're All Set!

Your trading bot is now production-ready with:
- ✅ Real-time data fetching
- ✅ Multiple trading strategies
- ✅ Risk management
- ✅ Comprehensive backtesting
- ✅ HTML report generation
- ✅ Data caching and persistence

Happy trading! 🚀📈
