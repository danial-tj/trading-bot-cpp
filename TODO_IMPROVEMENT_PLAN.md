> Historical pre-2.0 document. Its feature, build, API, test and performance claims are superseded by [the current README](README.md) and [verified progress](docs/PROGRESS.md). Network adapters are excluded from the offline release.

# Trading Bot - Comprehensive TODO & Improvement Plan

**Last Updated:** October 12, 2025  
**Status:** 9 Critical TODOs Identified  
**Priority Level:** HIGH

---

## Executive Summary

This document provides a comprehensive review of all TODO items found in the Trading Bot codebase. The analysis identifies **9 critical gaps** across 3 core components that need to be addressed for production readiness.

### Quick Stats
- **Total TODOs:** 9
- **Files Affected:** 3
- **Lines of Code to Implement:** ~300-500 (estimated)
- **Estimated Effort:** 2-3 days (1 developer)

---

## TODO Items by Priority

### Priority 1: Critical (Breaks Core Functionality)

#### 1.1 Calculate P&L for Trades
**File:** `src/backtester/backtester.cpp`  
**Line:** 164  
**Current Code:**
```cpp
// TODO: Calculate P&L for the trade
// For now, set to 0 - will be calculated in statistics
trade.pnl = 0.0;
```

**Issue:** All trades currently show `pnl = 0.0`, making backtest results meaningless.

**What's Needed:**
- Track entry price for each position
- Calculate P&L when position is closed: `(exit_price - entry_price) * quantity`
- Account for long vs short positions
- Include commission costs in P&L calculation

**Implementation Required:**
```cpp
// For BUY trades (opening position)
if (signal.type == SignalType::BUY) {
    trade.pnl = 0.0; // Opening trade has no P&L yet
    trade.entry_price = trade.price;
}

// For SELL trades (closing position)
if (signal.type == SignalType::SELL) {
    double gross_pnl = (trade.price - position_entry_price) * trade.quantity;
    trade.pnl = gross_pnl - trade.commission;
}
```

**Estimated Effort:** 2-3 hours

---

#### 1.2 Implement Stop-Loss and Take-Profit Logic
**File:** `src/risk/risk_manager.cpp`  
**Line:** 157-162  
**Current Code:**
```cpp
// TODO: Implement stop-loss and take-profit logic
// 1. Calculate current P&L
// 2. Check stop-loss threshold
// 3. Check take-profit threshold
// 4. Check time-based exits if needed
```

**Issue:** Basic implementation exists for long positions but lacks:
- Proper tracking of unrealized P&L
- Entry price tracking per position
- Multiple position management

**What's Needed:**
```cpp
bool RiskManager::should_close_position(const Position& position, 
                                       const MarketData& current_data,
                                       const PortfolioState& portfolio) {
    if (position.quantity == 0.0) return false;
    
    double current_price = current_data.close;
    double entry_price = position.avg_price;
    double pct_change = (current_price - entry_price) / entry_price;
    
    // Long position checks
    if (position.quantity > 0) {
        if (pct_change <= -risk_params_.stop_loss_pct) {
            LOG_INFO("Stop-loss triggered: " + std::to_string(pct_change * 100) + "%");
            return true;
        }
        if (pct_change >= risk_params_.take_profit_pct) {
            LOG_INFO("Take-profit triggered: " + std::to_string(pct_change * 100) + "%");
            return true;
        }
    }
    
    // Short position checks (TODO: currently missing - see #1.3)
    
    // Time-based exits (optional enhancement)
    // if (days_held > max_hold_period) return true;
    
    return false;
}
```

**Status:** Basic implementation exists for long positions  
**Missing:** Short position logic (see #1.3)  
**Estimated Effort:** 1-2 hours for improvements

---

#### 1.3 Add Logic for Short Positions
**File:** `src/risk/risk_manager.cpp`  
**Line:** 179  
**Current Code:**
```cpp
// TODO: Add logic for short positions
```

**Issue:** The system currently only handles long positions properly. Short selling is enabled in config but not fully implemented.

**What's Needed:**
```cpp
// Short position checks (quantity < 0 means short)
if (position.quantity < 0) {
    // For shorts, we profit when price goes DOWN
    // So invert the logic
    if (pct_change >= risk_params_.stop_loss_pct) {
        // Price went up - that's bad for shorts
        LOG_INFO("Short stop-loss triggered: " + std::to_string(pct_change * 100) + "%");
        return true;
    }
    if (pct_change <= -risk_params_.take_profit_pct) {
        // Price went down - that's profit for shorts
        LOG_INFO("Short take-profit triggered: " + std::to_string(-pct_change * 100) + "%");
        return true;
    }
}
```

**Estimated Effort:** 1-2 hours

---

### Priority 2: Important (Incomplete Statistics)

#### 2.1 Comprehensive Statistics Calculation
**File:** `src/backtester/backtester.cpp`  
**Line:** 178-183  
**Current Code:**
```cpp
// TODO: Implement comprehensive statistics calculation
// 1. Calculate total return
// 2. Calculate win rate
// 3. Calculate Sharpe ratio
// 4. Calculate maximum drawdown
// 5. Calculate profit factor
```

**Status:** 
- [DONE] Total return - Implemented
- [DONE] Win rate - Implemented
- [DONE] Max drawdown - Implemented
- [TODO] Sharpe ratio - Not calculated in results
- [TODO] Profit factor - Not calculated

**What's Missing:**

```cpp
void Backtester::calculate_statistics() {
    if (results_.trades.empty()) return;
    
    // ... existing code ...
    
    // Calculate Sharpe ratio from equity curve
    if (results_.equity_curve.size() > 1) {
        std::vector<double> returns;
        for (size_t i = 1; i < results_.equity_curve.size(); ++i) {
            double ret = (results_.equity_curve[i] - results_.equity_curve[i-1]) 
                        / results_.equity_curve[i-1];
            returns.push_back(ret);
        }
        results_.sharpe_ratio = calculate_sharpe_ratio(returns);
    }
    
    // Calculate profit factor
    double gross_profit = 0.0;
    double gross_loss = 0.0;
    for (const auto& trade : results_.trades) {
        if (trade.pnl > 0) {
            gross_profit += trade.pnl;
        } else if (trade.pnl < 0) {
            gross_loss += std::abs(trade.pnl);
        }
    }
    
    if (gross_loss > 0) {
        results_.profit_factor = gross_profit / gross_loss;
    } else {
        results_.profit_factor = (gross_profit > 0) ? 999.0 : 0.0;
    }
    
    // Calculate average win/loss
    if (results_.winning_trades > 0) {
        results_.avg_win = gross_profit / results_.winning_trades;
    }
    if (results_.losing_trades > 0) {
        results_.avg_loss = gross_loss / results_.losing_trades;
    }
}
```

**Estimated Effort:** 2-3 hours

---

#### 2.2 Calculate Additional Metrics
**File:** `src/backtester/backtester.cpp`  
**Line:** 212-216  
**Current Code:**
```cpp
// TODO: Calculate additional metrics
// - Annualized return
// - Sharpe ratio
// - Average win/loss
// - Profit factor
```

**What's Needed:**

```cpp
// Annualized return (assuming daily data)
if (!results_.equity_curve.empty() && results_.trades.size() > 1) {
    // Get first and last trade dates
    std::string start_date = results_.trades.front().timestamp;
    std::string end_date = results_.trades.back().timestamp;
    
    // Calculate days (simplified - would need proper date parsing)
    int days_held = results_.equity_curve.size();
    double years = days_held / 252.0; // 252 trading days per year
    
    if (years > 0) {
        double total_return_mult = (1.0 + results_.total_return);
        results_.annualized_return = std::pow(total_return_mult, 1.0 / years) - 1.0;
    }
}

// Annualize Sharpe ratio
if (results_.sharpe_ratio != 0) {
    results_.sharpe_ratio *= std::sqrt(252); // Assuming daily returns
}
```

**Estimated Effort:** 2-3 hours

---

### Priority 3: Nice to Have (Configuration & Polish)

#### 3.1 Use ReportGenerator
**File:** `src/trading_bot.cpp`  
**Line:** 135  
**Current Code:**
```cpp
// TODO: Use ReportGenerator when implemented
```

**Issue:** Currently using inline HTML generation. Should use the dedicated `ReportGenerator` class.

**What's Needed:**
```cpp
void TradingBot::generate_report(const std::string& output_file) {
    try {
        if (!report_generator_) {
            report_generator_ = std::make_unique<ReportGenerator>();
        }
        
        report_generator_->generate_html_report(results_, output_file);
        LOG_INFO("Report generated: " + output_file);
        
    } catch (const std::exception& e) {
        LOG_ERROR("Failed to generate report: " + std::string(e.what()));
    }
}
```

**Status:** Workaround exists - low priority  
**Estimated Effort:** 1 hour

---

#### 3.2 Proper JSON Config Parsing
**File:** `src/trading_bot.cpp`  
**Lines:** 360, 370  
**Current Code:**
```cpp
// TODO: Add proper JSON config parsing later
```

**Issue:** Currently using hardcoded defaults. Config file is read but not fully parsed.

**What's Needed:**
- Integrate a proper JSON library (nlohmann/json or RapidJSON)
- Parse all config sections properly
- Load strategy parameters from config
- Load risk management parameters from config
- Load backtesting parameters from config

**Current Workaround:** Basic key-value parsing exists but ignores most config

**Recommended Solution:**
```cpp
// Add to CMakeLists.txt
find_package(nlohmann_json REQUIRED)
target_link_libraries(trading_bot PRIVATE nlohmann_json::nlohmann_json)

// In trading_bot.cpp
#include <nlohmann/json.hpp>

RiskParameters TradingBot::load_risk_parameters() {
    RiskParameters params;
    
    // Parse from config_data_
    if (config_json_.contains("risk_management")) {
        auto& rm = config_json_["risk_management"];
        params.max_position_size = rm.value("max_position_size", 0.02);
        params.max_drawdown = rm.value("max_drawdown", 0.20);
        params.stop_loss_pct = rm.value("stop_loss_pct", 0.02);
        params.take_profit_pct = rm.value("take_profit_pct", 0.10);
        params.max_daily_loss = rm.value("max_daily_loss", 0.05);
    }
    
    return params;
}
```

**Estimated Effort:** 3-4 hours

---

#### 3.3 ATR-Based Position Sizing
**File:** `src/risk/risk_manager.cpp`  
**Line:** 77  
**Current Code:**
```cpp
// TODO: Modify function signature to accept historical data for ATR calculation
```

**Issue:** Position sizing is simplistic. Should use ATR (Average True Range) for volatility-adjusted position sizing.

**What's Needed:**
```cpp
// Update function signature
double RiskManager::calculate_position_size(
    const TradingSignal& signal, 
    const PortfolioState& portfolio,
    const std::vector<MarketData>& historical_data) {  // Add historical data
    
    if (signal.type == SignalType::HOLD) return 0.0;
    
    // Calculate ATR if we have enough data
    double atr = 0.0;
    if (historical_data.size() >= 14) {
        atr = calculate_atr(historical_data, 14);
    }
    
    // Position sizing based on ATR
    if (atr > 0) {
        // Risk 1% of portfolio per trade
        double risk_amount = portfolio.total_value * 0.01;
        
        // Use ATR multiple for stop distance
        double stop_distance = atr * risk_params_.position_sizing_atr;
        
        // Calculate position size
        double position_size = risk_amount / stop_distance;
        
        // Ensure we don't exceed max position size
        double max_shares = portfolio.total_value * risk_params_.max_position_size / signal.price;
        return std::min(position_size, max_shares);
    }
    
    // Fallback to simple sizing if ATR not available
    double max_risk_amount = portfolio.total_value * risk_params_.max_position_size;
    return max_risk_amount / signal.price;
}
```

**Note:** This requires updating the Backtester to pass historical data to the risk manager.

**Estimated Effort:** 2-3 hours

---

## Implementation Roadmap

### Phase 1: Critical Fixes (Week 1)
**Goal:** Make backtesting results accurate and meaningful

1. **Day 1:** Fix P&L calculation (#1.1)
   - Implement proper entry/exit price tracking
   - Test with sample data
   - Verify results match expected values

2. **Day 2:** Complete stop-loss/take-profit (#1.2 & #1.3)
   - Enhance existing logic
   - Add short position support
   - Add comprehensive logging
   - Test with both long and short strategies

3. **Day 3:** Complete statistics (#2.1 & #2.2)
   - Implement missing metrics (Sharpe ratio, profit factor)
   - Add annualized return calculation
   - Verify all metrics calculate correctly

**Deliverable:** Fully functional backtesting engine with accurate statistics

---

### Phase 2: Configuration & Polish (Week 2)
**Goal:** Make the system production-ready

4. **Day 4-5:** Implement proper JSON parsing (#3.2)
   - Integrate nlohmann/json library
   - Parse all config sections
   - Update documentation with config examples
   - Test all configuration options

5. **Day 6:** Integrate ReportGenerator (#3.1)
   - Use dedicated class instead of inline HTML
   - Add more report formats (CSV, JSON)
   - Enhance report visualizations

6. **Day 7:** ATR-based position sizing (#3.3)
   - Update function signatures
   - Implement ATR calculations
   - Test with volatile stocks

**Deliverable:** Production-ready system with full configuration support

---

### Phase 3: Testing & Documentation (Week 3)
**Goal:** Ensure reliability and maintainability

7. **Day 8-9:** Comprehensive testing
   - Unit tests for all new functionality
   - Integration tests
   - Edge case testing
   - Performance testing

8. **Day 10:** Documentation updates
   - Update DEVELOPMENT.md
   - Create examples for all features
   - Add code comments
   - Update README with new capabilities

**Deliverable:** Well-tested, well-documented system

---

## Testing Strategy

### Unit Tests Required

```cpp
// tests/test_risk_manager_pnl.cpp
TEST(RiskManagerTest, CalculatePnLLongPosition) {
    // Test P&L calculation for long positions
}

TEST(RiskManagerTest, CalculatePnLShortPosition) {
    // Test P&L calculation for short positions
}

TEST(RiskManagerTest, StopLossShortPosition) {
    // Test stop-loss triggers correctly for shorts
}

// tests/test_backtester_stats.cpp
TEST(BacktesterTest, CalculateSharpeRatio) {
    // Test Sharpe ratio calculation
}

TEST(BacktesterTest, CalculateProfitFactor) {
    // Test profit factor calculation
}

// tests/test_config_parsing.cpp
TEST(ConfigTest, ParseRiskParameters) {
    // Test risk parameter parsing from JSON
}

TEST(ConfigTest, ParseStrategyParameters) {
    // Test strategy parameter parsing
}
```

---

## 📝 Additional Recommendations

### 1. Code Quality Improvements
- Add more error handling and validation
- Improve logging throughout the system
- Add assertions for critical conditions
- Consider adding debug mode with verbose output

### 2. Architecture Enhancements
- Consider adding a `PositionManager` class to track multiple positions
- Separate concerns: `Portfolio` class vs `PortfolioState` struct
- Add observer pattern for real-time monitoring
- Consider event-driven architecture for better modularity

### 3. Performance Optimizations
- Profile the backtesting loop
- Consider parallel backtesting for multiple strategies
- Add data caching for frequently used calculations
- Optimize indicator calculations

### 4. Documentation Needs
- API documentation (Doxygen)
- Architecture diagrams
- Tutorial for adding new strategies
- Best practices guide

---

## 🎯 Success Metrics

After completing all TODOs, the system should achieve:

- ✅ **Accuracy:** P&L calculations match manual calculations
- ✅ **Completeness:** All backtest metrics implemented and tested
- ✅ **Flexibility:** Full configuration via JSON files
- ✅ **Reliability:** 90%+ test coverage
- ✅ **Performance:** Backtest 1 year of daily data in < 1 second
- ✅ **Maintainability:** Clear code structure and documentation

---

## 📚 References

- [x] Risk Management Best Practices: Van Tharp's Position Sizing
- [x] Statistics Formulas: Investopedia - Sharpe Ratio, Profit Factor
- [x] JSON Library: nlohmann/json (https://github.com/nlohmann/json)
- [x] Testing Framework: Google Test (already integrated)

---

## 🔄 Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | Oct 12, 2025 | Initial comprehensive TODO analysis |

---

**Next Action:** Review this plan and prioritize which phase to start with. Recommend starting with Phase 1 to get core functionality working correctly.

