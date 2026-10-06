> Historical pre-2.0 document. Its feature, build, API, test and performance claims are superseded by [the current README](README.md) and [verified progress](docs/PROGRESS.md). Network adapters are excluded from the offline release.

# Development Guide

This guide explains how to extend and modify the AI Trading Bot.

## Project Architecture

The trading bot follows a modular architecture with clear separation of concerns:

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   CSV Parser    │    │    Strategy     │    │  Risk Manager   │
│                 │    │                 │    │                 │
│ - Load data     │───▶│ - Generate      │───▶│ - Position      │
│ - Validate      │    │   signals       │    │   sizing        │
│ - Parse format  │    │ - Calculate     │    │ - Stop loss     │
└─────────────────┘    │   indicators    │    │ - Drawdown      │
                       └─────────────────┘    │   protection    │
                                              └─────────────────┘
                                                       │
                       ┌─────────────────┐            │
                       │   Backtester    │◀───────────┘
                       │                 │
                       │ - Simulate      │
                       │   trades        │
                       │ - Track P&L     │
                       │ - Calculate     │
                       │   metrics       │
                       └─────────────────┘
                                │
                       ┌─────────────────┐
                       │ Report Generator│
                       │                 │
                       │ - CSV output    │
                       │ - HTML reports  │
                       │ - Charts        │
                       └─────────────────┘
```

## Adding New Strategies

### 1. Create Strategy Header

Create a new header file in `include/strategy/`:

```cpp
#pragma once
#include "strategy/strategy.h"

namespace TradingBot {

class MyNewStrategy : public Strategy {
public:
    MyNewStrategy();
    
    bool initialize(const std::map<std::string, double>& params) override;
    TradingSignal generate_signal(const MarketData& data, 
                                 const Position& current_position) override;
    std::map<std::string, double> get_parameters() const override;
    bool validate_parameters(const std::map<std::string, double>& params) const override;
    
private:
    // Strategy-specific parameters
    double param1_;
    int param2_;
    
    // Strategy state
    std::vector<double> indicator_values_;
};

} // namespace TradingBot
```

### 2. Implement Strategy

Create the implementation in `src/strategy/`:

```cpp
#include "strategy/my_new_strategy.h"
#include <cmath>

namespace TradingBot {

MyNewStrategy::MyNewStrategy() 
    : Strategy("MyNewStrategy"), param1_(0.0), param2_(0) {
}

bool MyNewStrategy::initialize(const std::map<std::string, double>& params) {
    auto it1 = params.find("param1");
    auto it2 = params.find("param2");
    
    if (it1 == params.end() || it2 == params.end()) {
        return false;
    }
    
    param1_ = it1->second;
    param2_ = static_cast<int>(it2->second);
    
    return true;
}

TradingSignal MyNewStrategy::generate_signal(const MarketData& data,
                                            const Position& current_position) {
    TradingSignal signal;
    signal.timestamp = data.timestamp;
    
    // Your strategy logic here
    if (/* buy condition */) {
        signal.type = SignalType::BUY;
        signal.price = data.close;
        signal.quantity = 100.0; // Calculate based on risk management
        signal.reason = "Buy signal triggered";
    } else if (/* sell condition */) {
        signal.type = SignalType::SELL;
        signal.price = data.close;
        signal.quantity = current_position.quantity;
        signal.reason = "Sell signal triggered";
    } else {
        signal.type = SignalType::HOLD;
    }
    
    return signal;
}

std::map<std::string, double> MyNewStrategy::get_parameters() const {
    return {
        {"param1", param1_},
        {"param2", static_cast<double>(param2_)}
    };
}

bool MyNewStrategy::validate_parameters(const std::map<std::string, double>& params) const {
    auto it1 = params.find("param1");
    auto it2 = params.find("param2");
    
    if (it1 == params.end() || it2 == params.end()) {
        return false;
    }
    
    // Validate parameter ranges
    if (it1->second <= 0.0 || it2->second <= 0) {
        return false;
    }
    
    return true;
}

} // namespace TradingBot
```

### 3. Update CMakeLists.txt

Add your strategy to `src/CMakeLists.txt`:

```cmake
# Strategy library
add_library(strategy
    strategy/strategy.cpp
    strategy/sma_crossover_strategy.cpp
    strategy/my_new_strategy.cpp  # Add this line
)
```

### 4. Update Configuration

Add strategy parameters to `config.json`:

```json
{
    "strategies": {
        "MY_NEW_STRATEGY": {
            "param1": 0.5,
            "param2": 20,
            "description": "My New Trading Strategy"
        }
    }
}
```

## Adding New Indicators

Create utility functions in the Strategy base class:

```cpp
// In include/strategy/strategy.h
protected:
    // Technical indicators
    double calculate_bollinger_bands(const std::vector<MarketData>& data, 
                                   int period, double std_dev);
    double calculate_macd(const std::vector<MarketData>& data, 
                         int fast_period, int slow_period, int signal_period);
    double calculate_stochastic(const std::vector<MarketData>& data, 
                               int k_period, int d_period);
```

## Adding New Risk Management Features

Extend the RiskManager class:

```cpp
// In include/risk/risk_manager.h
public:
    // New risk management methods
    bool check_correlation_limit(const std::vector<std::string>& symbols);
    double calculate_var(const std::vector<double>& returns, double confidence);
    bool check_sector_exposure(const std::map<std::string, double>& positions);
```

## Adding New Report Formats

Extend the ReportGenerator:

```cpp
// In include/reporting/report_generator.h
public:
    // New report formats
    bool generate_json_report(const BacktestResults& results, const std::string& filename);
    bool generate_pdf_report(const BacktestResults& results, const std::string& filename);
    bool generate_excel_report(const BacktestResults& results, const std::string& filename);
```

## Testing New Features

### 1. Create Test File

Create a test file in `tests/`:

```cpp
#include <gtest/gtest.h>
#include "strategy/my_new_strategy.h"

class MyNewStrategyTest : public ::testing::Test {
protected:
    void SetUp() override {
        strategy_ = std::make_unique<TradingBot::MyNewStrategy>();
    }
    
    std::unique_ptr<TradingBot::MyNewStrategy> strategy_;
};

TEST_F(MyNewStrategyTest, InitializationTest) {
    std::map<std::string, double> params = {{"param1", 0.5}, {"param2", 20.0}};
    EXPECT_TRUE(strategy_->initialize(params));
}

TEST_F(MyNewStrategyTest, ParameterValidationTest) {
    std::map<std::string, double> invalid_params = {{"param1", -1.0}};
    EXPECT_FALSE(strategy_->validate_parameters(invalid_params));
}
```

### 2. Update Test CMakeLists.txt

Add your test to `tests/CMakeLists.txt`:

```cmake
add_executable(trading_bot_tests
    test_csv_parser.cpp
    test_strategy.cpp
    test_my_new_strategy.cpp  # Add this line
    # ... other tests
)
```

## Performance Optimization

### 1. Memory Management

- Use `std::vector` for dynamic arrays
- Pre-allocate vectors when size is known
- Use `std::move` for large objects
- Consider using `std::array` for fixed-size data

### 2. Algorithm Optimization

- Cache frequently calculated values
- Use efficient data structures (maps for lookups, vectors for iteration)
- Minimize memory allocations in tight loops
- Consider using SIMD instructions for calculations

### 3. I/O Optimization

- Buffer file operations
- Use binary formats for large datasets
- Implement data compression for historical data
- Use memory-mapped files for very large datasets

## Debugging Tips

### 1. Logging

Use the built-in logger for debugging:

```cpp
LOG_DEBUG("Strategy state: " + std::to_string(param1_));
LOG_INFO("Signal generated: " + signal.reason);
LOG_WARNING("Risk limit approaching: " + std::to_string(current_risk));
LOG_ERROR("Failed to execute trade: " + error_message);
```

### 2. Assertions

Add assertions for critical conditions:

```cpp
#include <cassert>

void MyStrategy::update(const MarketData& data) {
    assert(data.close > 0.0 && "Price must be positive");
    assert(data.volume >= 0.0 && "Volume must be non-negative");
    
    // ... rest of the method
}
```

### 3. Unit Testing

Test individual components in isolation:

```cpp
TEST_F(StrategyTest, EdgeCases) {
    // Test with zero prices
    MarketData zero_data = {0.0, 0.0, 0.0, 0.0, 0.0};
    EXPECT_THROW(strategy_->generate_signal(zero_data, position_), std::invalid_argument);
    
    // Test with negative prices
    MarketData negative_data = {-100.0, -99.0, -101.0, -100.0, 1000};
    EXPECT_THROW(strategy_->generate_signal(negative_data, position_), std::invalid_argument);
}
```

## Code Style Guidelines

### 1. Naming Conventions

- Classes: `PascalCase` (e.g., `TradingStrategy`)
- Methods: `snake_case` (e.g., `calculate_sma`)
- Variables: `snake_case` (e.g., `current_price`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `MAX_POSITION_SIZE`)

### 2. Documentation

- Use Doxygen-style comments for public interfaces
- Document complex algorithms
- Include examples in documentation
- Keep comments up-to-date with code changes

### 3. Error Handling

- Use exceptions for exceptional conditions
- Return error codes for expected failures
- Log all errors with context
- Provide meaningful error messages

## Next Steps

1. **Implement Core Components**: Start with the CSV parser and basic strategy
2. **Add Unit Tests**: Ensure each component works correctly
3. **Integrate Components**: Connect all pieces together
4. **Test with Real Data**: Use historical market data for validation
5. **Optimize Performance**: Profile and optimize bottlenecks
6. **Add ML Features**: Integrate machine learning algorithms
7. **Paper Trading**: Simulate live trading without real money

Remember: Start simple and iterate. The modular design makes it easy to add features incrementally.
