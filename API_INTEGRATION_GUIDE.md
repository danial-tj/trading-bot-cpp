> Historical pre-2.0 document. Its feature, build, API, test and performance claims are superseded by [the current README](README.md) and [verified progress](docs/PROGRESS.md). Network adapters are excluded from the offline release.

# API Integration Guide

This guide explains how to use the new API data fetching capabilities in your Trading Bot.

## Overview

The Trading Bot now supports fetching real-time market data from external APIs, eliminating the need for manual CSV file downloads. You can:

- **Fetch historical market data** from Yahoo Finance or Alpha Vantage
- **Run backtests directly** with live API data
- **Download and save data** for offline use
- **Cache data** to avoid redundant API calls

## Supported Data Providers

### 1. Yahoo Finance (Default)
- **API Key Required:** NO ✓
- **Rate Limits:** Generous (no official limit)
- **Supported Symbols:** Stocks, ETFs, Crypto (with -USD suffix)
- **Data Intervals:** Daily, Weekly, Monthly
- **Best For:** Quick start, no setup required

### 2. Alpha Vantage
- **API Key Required:** YES (free tier available)
- **Rate Limits:** 25 requests/day (free), 5 requests/minute
- **Supported Symbols:** Stocks, Forex, Crypto
- **Data Intervals:** 1min, 5min, 15min, 30min, 60min, Daily, Weekly, Monthly
- **Best For:** Intraday data, more detailed historical data
- **Get Free Key:** https://www.alphavantage.co/support/#api-key

## Quick Start

### Option 1: Using Yahoo Finance (No Setup Required)

```cpp
#include "trading_bot.h"

int main() {
    TradingBot::TradingBot bot;
    bot.initialize("config.json");
    
    // Run backtest directly with API data
    bot.run_backtest_with_api(
        "AAPL",              // Symbol
        "SMA_CROSSOVER",     // Strategy
        "2024-01-01",        // Start date
        "2024-10-07",        // End date
        DataInterval::DAILY  // Interval (optional)
    );
    
    bot.generate_report("AAPL_report.html");
    return 0;
}
```

### Option 2: Using Alpha Vantage (Requires API Key)

1. **Get your free API key** from Alpha Vantage:
   - Visit: https://www.alphavantage.co/support/#api-key
   - Takes 30 seconds to sign up

2. **Update config.json:**
```json
{
    "api": {
        "default_provider": "alpha_vantage",
        "alpha_vantage": {
            "api_key": "YOUR_API_KEY_HERE",
            "enabled": true
        }
    }
}
```

3. **Use in your code:**
```cpp
TradingBot::TradingBot bot;
bot.initialize("config.json");
bot.set_api_provider(APIProvider::ALPHA_VANTAGE);

// Now fetch intraday data
bot.run_backtest_with_api(
    "IBM",
    "RSI",
    "2024-09-01",
    "2024-10-07",
    DataInterval::MINUTE_15  // 15-minute intervals
);
```

## Usage Examples

### Example 1: Fetch and Save Data

Download data to CSV file for later use:

```cpp
#include "trading_bot.h"

int main() {
    TradingBot::TradingBot bot;
    bot.initialize("config.json");
    
    // Fetch and save MSFT data
    bot.fetch_market_data(
        "MSFT",                          // Symbol
        "2023-01-01",                    // Start date
        "2024-10-07",                    // End date
        "data/MSFT_2023_2024.csv"        // Output file
    );
    
    // Now use the saved CSV file
    bot.run_backtest("data/MSFT_2023_2024.csv", "EMA_CROSSOVER");
    
    return 0;
}
```

### Example 2: Multi-Symbol Analysis

Compare strategies across multiple stocks:

```cpp
#include "trading_bot.h"
#include <vector>

int main() {
    TradingBot::TradingBot bot;
    bot.initialize("config.json");
    
    std::vector<std::string> symbols = {"AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"};
    std::string strategy = "SMA_CROSSOVER";
    
    for (const auto& symbol : symbols) {
        std::cout << "Testing " << symbol << "..." << std::endl;
        
        bot.run_backtest_with_api(
            symbol,
            strategy,
            "2024-01-01",
            "2024-10-07"
        );
        
        auto results = bot.get_results();
        std::cout << "Return: " << (results.total_return * 100) << "%" << std::endl;
        
        bot.generate_report(symbol + "_" + strategy + "_report.html");
    }
    
    return 0;
}
```

### Example 3: Using the API Data Fetcher Directly

For more control, use the APIDataFetcher class directly:

```cpp
#include "data/api_data_fetcher.h"
#include <iostream>

int main() {
    TradingBot::APIDataFetcher fetcher;
    
    // Initialize with Yahoo Finance
    std::map<std::string, std::string> config;
    fetcher.initialize(config);
    fetcher.set_provider(APIProvider::YAHOO_FINANCE);
    
    // Fetch data
    auto response = fetcher.fetch_data(
        "AAPL",
        DataInterval::DAILY,
        "2024-01-01",
        "2024-10-07"
    );
    
    if (response.success) {
        std::cout << "Fetched " << response.data.size() << " data points" << std::endl;
        
        // Access individual data points
        for (const auto& data : response.data) {
            std::cout << data.timestamp << ": "
                      << "Close=" << data.close << ", "
                      << "Volume=" << data.volume << std::endl;
        }
        
        // Save to CSV
        fetcher.save_to_csv(response, "my_data.csv");
    }
    
    return 0;
}
```

## Configuration

### Complete API Configuration (config.json)

```json
{
    "trading_bot": {
        "name": "AI_Trading_Bot",
        "version": "2.0.0"
    },
    
    "api": {
        "default_provider": "yahoo_finance",
        
        "alpha_vantage": {
            "api_key": "YOUR_KEY_HERE",
            "enabled": true,
            "rate_limit_per_minute": 5,
            "timeout_seconds": 30
        },
        
        "yahoo_finance": {
            "enabled": true,
            "timeout_seconds": 30
        },
        
        "cache_enabled": true,
        "auto_save_csv": true,
        "csv_output_directory": "./data/api_downloads"
    },
    
    "backtesting": {
        "initial_capital": 100000.0,
        "commission_rate": 0.001
    }
}
```

## Available Data Intervals

- `DataInterval::MINUTE_1` - 1-minute bars (Alpha Vantage only)
- `DataInterval::MINUTE_5` - 5-minute bars (Alpha Vantage only)
- `DataInterval::MINUTE_15` - 15-minute bars (Alpha Vantage only)
- `DataInterval::MINUTE_30` - 30-minute bars (Alpha Vantage only)
- `DataInterval::HOUR_1` - 1-hour bars (Alpha Vantage only)
- `DataInterval::DAILY` - Daily bars (Both providers)
- `DataInterval::WEEKLY` - Weekly bars (Both providers)
- `DataInterval::MONTHLY` - Monthly bars (Both providers)

## Supported Symbols

### US Stocks
- Major stocks: `AAPL`, `MSFT`, `GOOGL`, `AMZN`, `TSLA`, `NVDA`, etc.
- Any NYSE or NASDAQ listed stock

### ETFs
- `SPY` - S&P 500
- `QQQ` - NASDAQ 100
- `IWM` - Russell 2000
- `VTI` - Total Stock Market
- And many more...

### Cryptocurrencies (Yahoo Finance)
- `BTC-USD` - Bitcoin
- `ETH-USD` - Ethereum
- `DOGE-USD` - Dogecoin
- Format: `{SYMBOL}-USD`

## Caching

The API data fetcher includes built-in caching to avoid redundant API calls:

```cpp
APIDataFetcher fetcher;
fetcher.initialize(config);
fetcher.enable_caching(true);  // Enable caching

// First fetch - calls API
auto data1 = fetcher.fetch_data("AAPL", DataInterval::DAILY, "2024-01-01", "2024-10-07");

// Second fetch - uses cache (instant)
auto data2 = fetcher.fetch_data("AAPL", DataInterval::DAILY, "2024-01-01", "2024-10-07");

// Clear cache when needed
fetcher.clear_cache();
```

## Error Handling

Always check for errors when fetching data:

```cpp
APIResponse response = fetcher.fetch_data("AAPL", DataInterval::DAILY, "2024-01-01", "2024-10-07");

if (!response.success) {
    std::cerr << "Error: " << response.error_message << std::endl;
    return 1;
}

if (response.data.empty()) {
    std::cerr << "No data returned" << std::endl;
    return 1;
}

// Process data...
```

## Common Issues and Solutions

### Issue 1: "API rate limit exceeded"
**Solution:** 
- Wait 1 minute before trying again
- Enable caching to reduce API calls
- Consider upgrading to Alpha Vantage premium

### Issue 2: "Invalid API key"
**Solution:**
- Check that your API key is correct in config.json
- Verify the API key is active at alphavantage.co
- Try using Yahoo Finance instead (no API key needed)

### Issue 3: "Failed to fetch data"
**Solution:**
- Check your internet connection
- Verify the symbol is valid
- Check date format (must be YYYY-MM-DD)
- Try Yahoo Finance as alternative

### Issue 4: Symbol not found
**Solution:**
- Verify symbol spelling
- For crypto on Yahoo Finance, use format: BTC-USD
- Some symbols may only be available on certain providers

## Testing the API Integration

### Test 1: Basic API Fetcher
```bash
cd build
./bin/Release/test_api_data_fetcher.exe
```
This tests the raw API fetching capabilities.

### Test 2: Full Integration with TradingBot
```bash
cd build
./bin/Release/test_trading_bot_with_api.exe
```
This demonstrates complete integration with backtesting.

## Performance Tips

1. **Use caching** - Avoid fetching the same data multiple times
2. **Download data first** - For multiple backtests, fetch once and save to CSV
3. **Batch requests** - When testing multiple symbols, add delays between requests
4. **Use daily data** - Intraday data uses more API quota
5. **Yahoo Finance first** - Start with Yahoo Finance before using Alpha Vantage quota

## Next Steps

1. ✅ **Test the basic API fetcher**: Run `test_api_data_fetcher.exe`
2. ✅ **Try a simple backtest**: Run `test_trading_bot_with_api.exe`
3. 📊 **Get real data**: Fetch data for your favorite stocks
4. 🔑 **Optional**: Get Alpha Vantage key for intraday data
5. 🚀 **Build your strategy**: Use real data to develop and test strategies

## Resources

- **Alpha Vantage API Docs**: https://www.alphavantage.co/documentation/
- **Yahoo Finance**: https://finance.yahoo.com/
- **Symbol Lookup**: https://finance.yahoo.com/lookup
- **Trading Bot GitHub**: https://github.com/Teejay021/trading-bot-cpp

## Support

If you encounter issues:
1. Check this guide first
2. Review error messages carefully
3. Test with Yahoo Finance first (simpler, no API key)
4. Verify your configuration in config.json
5. Check the logs for detailed error information

Happy Trading! 🚀📈
