#include "backtester/backtester.h"
#include <cmath>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

using namespace TradingBot;
namespace {
int passed = 0;
void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}
void near(double actual, double expected) {
    require(std::abs(actual - expected) < 0.000001, "expected " + std::to_string(expected) + ", got " + std::to_string(actual));
}
template<class F> void throws(F operation) {
    bool thrown = false;
    try { operation(); } catch (const std::exception&) { thrown = true; }
    require(thrown, "operation should throw");
}
void test(const std::string& name, const std::function<void()>& operation) {
    try { operation(); ++passed; std::cout << "PASS " << name << '\n'; }
    catch (const std::exception& error) { throw std::runtime_error(name + ": " + error.what()); }
}
MarketData bar(std::string timestamp, double open = 100, double close = 100) {
    MarketData b;
    b.timestamp = std::move(timestamp); b.open = open; b.close = close;
    b.high = std::max(open, close); b.low = std::min(open, close); b.volume = 1000;
    return b;
}
TradingSignal signal(SignalType type, double quantity = 0) {
    TradingSignal s; s.type = type; s.quantity = quantity; s.price = 100; s.reason = "scripted fixture"; return s;
}
class ScriptStrategy : public Strategy {
public:
    explicit ScriptStrategy(std::vector<TradingSignal> signals) : Strategy("Fixture"), signals_(std::move(signals)) {}
    bool initialize(const std::map<std::string, double>&) override { index_ = 0; return true; }
    TradingSignal generate_signal(const MarketData&, const Position&) override {
        return index_ < signals_.size() ? signals_[index_++] : TradingSignal{};
    }
    std::map<std::string, double> get_parameters() const override { return {}; }
    bool validate_parameters(const std::map<std::string, double>&) const override { return true; }
private:
    std::vector<TradingSignal> signals_;
    size_t index_ = 0;
};
class ExplodingStrategy : public ScriptStrategy {
public:
    ExplodingStrategy() : ScriptStrategy({}) {}
    TradingSignal generate_signal(const MarketData&, const Position&) override { throw std::runtime_error("visible strategy failure"); }
};
RiskParameters permissive_risk() {
    RiskParameters p; p.max_position_size = 1; p.max_daily_loss = 1; p.max_drawdown = 1;
    p.stop_loss_pct = 0; p.take_profit_pct = 0; return p;
}
BacktestConfig config(double capital = 10000) {
    BacktestConfig c; c.initial_capital = capital; c.commission_rate = 0; c.slippage = 0; return c;
}
BacktestResults run(std::vector<MarketData> records, std::vector<TradingSignal> signals,
                    BacktestConfig c = config(), RiskParameters p = permissive_risk()) {
    auto parser = std::make_shared<CSVParser>();
    require(parser->load_records(records), parser->get_last_error());
    auto risk = std::make_shared<RiskManager>(); require(risk->initialize(p), "risk init");
    Backtester engine; require(engine.initialize(c), "engine init");
    return engine.run_backtest(std::make_shared<ScriptStrategy>(signals), parser, risk);
}
}
int main() {
try {
    test("cash plus holdings and net round-trip fees", [] {
        Ledger l(10000); l.buy(10, 100, 1, "buy");
        require(l.cash_cents() == 899900 && l.quantity() == 10, "buy accounting");
        require(l.equity_cents(100) == 999900, "equity includes holdings");
        l.sell(10, 110, 1, "sell");
        require(l.cash_cents() == 1009800 && l.quantity() == 0, "sell accounting");
        require(l.realized_pnl_cents() == 9800 && l.cost_basis_cents() == 0, "net realized fees");
    });
    test("weighted cost basis and partial exit allocation", [] {
        Ledger l(10000); l.buy(10, 100, 1, "b1"); l.buy(10, 200, 1, "b2");
        near(l.average_cost(), 150.1);
        l.sell(5, 180, 1, "s1");
        require(l.quantity() == 15 && l.cost_basis_cents() == 225150, "remaining weighted basis");
        require(l.realized_pnl_cents() == 14850, "partial exit pnl");
        l.sell(15, 180, 1, "s2");
        require(l.cost_basis_cents() == 0 && l.realized_pnl_cents() == 59600, "all basis released");
    });
    test("fractional-cent partial basis rounds exactly and final exit releases residue", [] {
        Ledger l(100); l.buy(3, 1, .01, "b"); l.sell(1, 1, 0, "s1");
        require(l.cost_basis_cents() == 201, "one-third basis rounds to 100 cents");
        l.sell(1, 1, 0, "s2");
        require(l.cost_basis_cents() == 100, "half remaining basis rounds up to 101 cents");
        l.sell(1, 1, 0, "s3");
        require(l.realized_pnl_cents() == -1, "round-trip basis reconciliation");
    });
    test("insufficient funds rejects atomically", [] {
        Ledger l(100); const auto n = l.events().size();
        throws([&] { l.buy(1, 100, 1, "x"); });
        require(l.cash_cents() == 10000 && !l.quantity() && l.events().size() == n, "no partial buy");
    });
    test("overselling rejects atomically", [] {
        Ledger l(1000); l.buy(2, 100, 0, "b"); const auto n = l.events().size();
        throws([&] { l.sell(3, 100, 0, "s"); });
        require(l.cash_cents() == 80000 && l.quantity() == 2 && l.events().size() == n, "no partial sale");
    });
    test("money overflow and nonfinite amounts fail", [] {
        throws([] { to_cents(std::numeric_limits<double>::infinity()); });
        throws([] { to_cents(1e30); });
        throws([] { checked_add(std::numeric_limits<Cents>::max(), 1); });
        throws([] { checked_multiply(std::numeric_limits<Cents>::max(), 2); });
        throws([] { Ledger l(.001); });
        Ledger l(100); throws([&] { l.buy(1, .001, 0, "x"); });
        require(l.cash_cents() == 10000, "zero-rounded price unchanged");
    });
    test("half-cent monetary rounding", [] {
        require(to_cents(1.125) == 113 && to_cents(-1.125) == -113, "half away from zero");
        require(to_cents(1.005) == 101 && to_cents(-1.005) == -101, "decimal ties do not inherit binary noise");
        require(to_cents(2.675) == 268 && to_cents(.005) == 1 && to_cents(.004999) == 0, "cent boundaries");
        require(to_cents(1e10) == 1000000000000LL && to_cents(1e-10) == 0, "scientific notation");
    });
    test("exact proportional basis with large values", [] {
        Ledger l(1000000000000.0); l.buy(3000000, 100000.01, 0.01, "b");
        const auto before = l.cost_basis_cents();
        l.sell(1000000, 100000.01, 0, "s");
        require(l.cost_basis_cents() == before - (before / 3 + (before % 3 >= 2 ? 1 : 0)), "large proportional integer allocation");
    });
    test("next-open prices and final net accounting", [] {
        auto c = config(); c.commission_fixed = 1;
        const auto r = run({bar("2025-01-01", 50, 90), bar("2025-01-02", 100), bar("2025-01-03", 110, 110)},
                           {signal(SignalType::BUY, 10), signal(SignalType::SELL, 10)}, c);
        require(r.trades.size() == 2 && r.closed_trades == 1, "fill and exit counts");
        near(r.trades[0].price, 100); near(r.trades[1].price, 110); near(r.final_cash, 10098);
        near(r.total_return, .0098); near(r.win_rate, 1); near(r.realized_pnl, 98);
        require(r.trades[0].signal_timestamp == "2025-01-01" && r.trades[0].timestamp == "2025-01-02", "signal precedes fill");
    });
    test("commissions and slippage use actual execution", [] {
        auto c = config(); c.commission_fixed = 1; c.commission_rate = .001; c.slippage = .01;
        auto r = run({bar("2025-01-01"), bar("2025-01-02", 100, 101)}, {signal(SignalType::BUY, 10)}, c);
        near(r.trades.at(0).price, 101); near(r.trades[0].commission, 2.01);
        near(r.final_cash, 8987.99); near(r.final_equity, 9997.99); near(r.total_slippage, 10);
        require(r.cost_basis_cents == 101201, "fees in cost basis");
    });
    test("open final holdings marked without fabricated liquidation", [] {
        auto r = run({bar("2025-01-01"), bar("2025-01-02", 100, 120)}, {signal(SignalType::BUY, 10)});
        require(r.trades.size() == 1 && r.final_quantity == 10 && r.closed_trades == 0, "retains holdings");
        near(r.final_cash, 9000); near(r.final_equity, 10200); near(r.unrealized_pnl, 200);
    });
    test("last-bar signal is cancelled", [] {
        auto r = run({bar("2025-01-01")}, {signal(SignalType::BUY, 10)});
        require(r.trades.empty() && r.rejections.size() == 1, "last order unfilled");
        near(r.final_equity, 10000);
    });
    test("future bar perturbation cannot change earlier fills", [] {
        std::vector<MarketData> a = {bar("2025-01-01"), bar("2025-01-02", 110, 110), bar("2025-01-03", 120, 120)};
        auto b = a; b[2] = bar("2025-01-03", 200, 200);
        auto r1 = run(a, {signal(SignalType::BUY, 10)});
        auto r2 = run(b, {signal(SignalType::BUY, 10)});
        require(r1.trades[0].price == r2.trades[0].price && r1.trades[0].timestamp == r2.trades[0].timestamp, "causal fill");
        require(r1.equity_curve[2] == r2.equity_curve[2], "causal equity history");
    });
    test("close-based stop executes at next gap open", [] {
        auto p = permissive_risk(); p.stop_loss_pct = .05;
        auto r = run({bar("2025-01-01"), bar("2025-01-02", 100, 90), bar("2025-01-03", 70, 70)},
                     {signal(SignalType::BUY, 10)}, config(), p);
        require(r.trades.size() == 2, "stop exits");
        near(r.trades[1].price, 70); near(r.final_equity, 9700);
        require(r.trades[1].reason.find("stop loss") != std::string::npos, "reason retained");
    });
    test("intrabar lows do not fabricate unavailable stop path", [] {
        auto p = permissive_risk(); p.stop_loss_pct = .05;
        auto middle = bar("2025-01-02"); middle.low = 50;
        auto r = run({bar("2025-01-01"), middle, bar("2025-01-03")}, {signal(SignalType::BUY, 10)}, config(), p);
        require(r.trades.size() == 1 && r.final_quantity == 10, "stops use completed close");
    });
    test("daily loss closes exposure and blocks reentry during same day", [] {
        auto p = permissive_risk(); p.max_daily_loss = .05;
        auto r = run({bar("2025-01-01T09:30"), bar("2025-01-01T09:32", 100, 90),
                      bar("2025-01-01T09:34", 80, 80), bar("2025-01-01T09:36", 80, 80)},
                     {signal(SignalType::BUY, 10), {}, signal(SignalType::BUY, 1)}, config(1000), p);
        require(r.trades.size() == 2 && r.final_quantity == 0, "forced risk reduction executes");
        near(r.final_equity, 800);
        require(!r.rejections.empty() && r.rejections[0].reason.find("daily loss") != std::string::npos, "reentry blocked");
    });
    test("daily loss resets at new session", [] {
        auto p = permissive_risk(); p.max_daily_loss = .05;
        auto r = run({bar("2025-01-01T09:30"), bar("2025-01-01T09:32", 100, 90),
                      bar("2025-01-01T09:34", 80, 80), bar("2025-01-02T09:30", 80, 80), bar("2025-01-02T09:32", 80, 80)},
                     {signal(SignalType::BUY, 10), {}, {}, signal(SignalType::BUY, 1)}, config(1000), p);
        require(r.trades.size() == 3 && r.final_quantity == 1, "next-day entries allowed");
    });
    test("maximum drawdown guards entry but permits exit", [] {
        auto p = permissive_risk(); p.max_drawdown = .05;
        auto r = run({bar("2025-01-01"), bar("2025-01-02", 100, 90), bar("2025-01-03", 80, 80),
                      bar("2025-01-04", 80, 80)}, {signal(SignalType::BUY, 10), {}, signal(SignalType::BUY, 1)}, config(1000), p);
        require(r.trades.size() == 2 && r.final_quantity == 0, "drawdown closure");
        require(r.rejections.at(0).reason.find("drawdown") != std::string::npos, "drawdown reentry blocked");
    });
    test("explicit oversizing and unavailable cash are rejected", [] {
        auto p = permissive_risk(); p.max_position_size = .05;
        auto r = run({bar("2025-01-01"), bar("2025-01-02")}, {signal(SignalType::BUY, 10)}, config(), p);
        require(r.trades.empty() && r.rejections.at(0).reason.find("position size") != std::string::npos, "exposure cap");
        auto c = config(1000); c.commission_fixed = 1;
        r = run({bar("2025-01-01"), bar("2025-01-02")}, {signal(SignalType::BUY, 10)}, c);
        require(r.trades.empty() && r.rejections.at(0).reason.find("cash") != std::string::npos, "fees cannot overspend");
    });
    test("automatic size reserves enough cash for fees", [] {
        auto c = config(1000); c.commission_fixed = 1;
        auto r = run({bar("2025-01-01"), bar("2025-01-02")}, {signal(SignalType::BUY)}, c);
        require(r.final_quantity == 9 && r.final_cash_cents == 9900, "whole-share affordability");
    });
    test("automatic sizing uses the same cent rounding as fees", [] {
        auto c = config(100); c.commission_fixed = .004;
        auto r = run({bar("2025-01-01", 1, 1), bar("2025-01-02", 1, 1)}, {signal(SignalType::BUY)}, c);
        require(r.final_quantity == 100 && r.final_cash_cents == 0, "subcent fee rounded to zero does not discard a share");
        c.commission_fixed = .005;
        r = run({bar("2025-01-01", 1, 1), bar("2025-01-02", 1, 1)}, {signal(SignalType::BUY)}, c);
        require(r.final_quantity == 99 && r.final_cash_cents == 99, "half-cent fee reserves one cent");
    });
    test("whole-share sizing divides fixed cents without binary truncation", [] {
        auto r = run({bar("2025-01-01", .1, .1), bar("2025-01-02", .1, .1)}, {signal(SignalType::BUY)}, config(.3));
        require(r.final_quantity == 3 && r.final_cash_cents == 0, "30 cents buys three 10-cent shares");
    });
    test("sell-all preserves owned size instead of entry sizing", [] {
        auto r = run({bar("2025-01-01"), bar("2025-01-02"), bar("2025-01-03")},
                     {signal(SignalType::BUY, 10), signal(SignalType::SELL)});
        require(r.trades.at(1).quantity == 10 && r.final_quantity == 0, "sell all");
    });
    test("excessive and fractional sales fail without state mutation", [] {
        auto r = run({bar("2025-01-01"), bar("2025-01-02"), bar("2025-01-03")},
                     {signal(SignalType::BUY, 10), signal(SignalType::SELL, 11)});
        require(r.trades.size() == 1 && r.final_quantity == 10 && r.rejections.size() == 1, "excess sale");
        r = run({bar("2025-01-01"), bar("2025-01-02")}, {signal(SignalType::BUY, .5)});
        require(r.trades.empty() && r.rejections.size() == 1, "fractional shares unsupported");
    });
    test("short and cover signals explicitly recorded as unsupported", [] {
        auto r = run({bar("2025-01-01"), bar("2025-01-02")}, {signal(SignalType::SHORT, 10), signal(SignalType::COVER, 10)});
        require(r.trades.empty() && r.rejections.size() == 2, "short signals retained");
    });
    test("opening signal expires at exclusive deadline", [] {
        auto s = signal(SignalType::BUY, 10); s.valid_until = "2025-01-01T09:32:00";
        auto r = run({bar("2025-01-01T09:30"), bar("2025-01-01T09:32")}, {s});
        require(r.trades.empty() && r.rejections.at(0).reason.find("expired") != std::string::npos, "deadline enforced");
    });
    test("opening signal cannot execute after overnight missing bars", [] {
        auto s = signal(SignalType::BUY, 10); s.valid_until = "2025-01-01T10:00:00";
        auto r = run({bar("2025-01-01T09:30"), bar("2025-01-02T09:30")}, {s});
        require(r.trades.empty() && r.rejections.size() == 1, "no next-day entry");
    });
    test("execution-bar later OHLCV cannot change the earlier open fill", [] {
        auto later = bar("2025-01-02", 100, 200); later.low = 50; later.volume = 0;
        auto a = run({bar("2025-01-01"), bar("2025-01-02")}, {signal(SignalType::BUY, 10)});
        auto b = run({bar("2025-01-01"), later}, {signal(SignalType::BUY, 10)});
        require(a.trades.size() == 1 && b.trades.size() == 1 && a.trades[0].price == b.trades[0].price &&
                a.trades[0].cash_after == b.trades[0].cash_after, "open fill does not use future aggregate volume or later prices");
    });
    test("repeatability resets same strategy and risk instances", [] {
        auto parser = std::make_shared<CSVParser>(); require(parser->load_records({bar("2025-01-01"), bar("2025-01-02")}), "load");
        auto strategy = std::make_shared<ScriptStrategy>(std::vector<TradingSignal>{signal(SignalType::BUY, 10)});
        auto risk = std::make_shared<RiskManager>(); require(risk->initialize(permissive_risk()), "risk");
        Backtester engine; require(engine.initialize(config()), "init");
        auto a = engine.run_backtest(strategy, parser, risk);
        engine.set_config(config(50000)); (void)engine.run_backtest(strategy, parser, risk);
        engine.set_config(config()); auto b = engine.run_backtest(strategy, parser, risk);
        require(a.final_cash_cents == b.final_cash_cents && a.final_quantity == b.final_quantity &&
                a.equity_curve == b.equity_curve && a.events.size() == b.events.size(), "runs are isolated");
    });
    test("start-date warms indicators while suppressing historical orders", [] {
        auto c = config(); c.start_date = "2025-01-02"; c.end_date = "2025-01-03";
        auto r = run({bar("2025-01-01"), bar("2025-01-02"), bar("2025-01-03"), bar("2025-01-04")},
                     {signal(SignalType::BUY, 20), signal(SignalType::BUY, 10)}, c);
        require(r.trades.size() == 1 && r.trades[0].quantity == 10 && r.trades[0].timestamp == "2025-01-03", "warmup respected");
        require(r.equity_curve.size() == 3, "range valuation count");
    });
    test("zero trades has valid equity and unavailable metrics", [] {
        auto r = run({bar("2025-01-01"), bar("2025-01-02")}, {});
        near(r.final_equity, 10000); near(r.total_return, 0); near(r.max_drawdown, 0);
        require(std::isnan(r.sharpe_ratio) && std::isnan(r.annualized_return), "unsupported metrics unavailable");
    });
    test("initial equity is included in maximum drawdown", [] {
        auto c = config(1000); c.commission_fixed = 10;
        auto r = run({bar("2025-01-01"), bar("2025-01-02")}, {signal(SignalType::BUY, 1)}, c);
        near(r.max_drawdown, .01);
    });
    test("errors are distinct from no trades", [] {
        auto parser = std::make_shared<CSVParser>(); auto risk = std::make_shared<RiskManager>();
        Backtester engine;
        throws([&] { engine.run_backtest(std::make_shared<ScriptStrategy>(std::vector<TradingSignal>{}), parser, risk); });
        require(parser->load_records({bar("2025-01-01")}), "load");
        throws([&] { engine.run_backtest(std::make_shared<ExplodingStrategy>(), parser, risk); });
    });
    test("invalid config cannot bypass validation", [] {
        Backtester engine; auto c = config(); c.enable_short_selling = true; require(!engine.initialize(c), "short toggle rejected");
        c = config(); c.slippage = std::numeric_limits<double>::quiet_NaN(); require(!engine.initialize(c), "nonfinite config");
        c = config(); c.start_date = "2025-02-29"; require(!engine.initialize(c), "invalid date");
        c = config(); c.commission_fixed = -1; throws([&] { engine.set_config(c); });
    });
    test("strict parser rejects empty duplicate unordered and mixed dates", [] {
        CSVParser p;
        require(!p.load_records({}), "empty");
        require(!p.load_records({bar("2025-01-01"), bar("2025-01-01")}), "duplicate");
        require(!p.load_records({bar("2025-01-02"), bar("2025-01-01")}), "unordered");
        require(!p.load_records({bar("2025-01-01"), bar("2025-01-02T09:30")}), "mixed formats");
        require(p.get_data_count() == 0 && !p.get_last_error().empty(), "failed load clears stale data");
    });
    test("strict parser rejects malformed Gregorian dates and clock time", [] {
        CSVParser p;
        for (const auto& s : {"2025-02-29", "2024-13-01", "2024-00-01", "2024-01-00", "2025-01-01T25:00", "2025-01-01T09:60", "2025-01-01T09:30:60", "2025-01-01T09:30Z"})
            require(!p.load_records({bar(s)}), s);
        require(p.load_records({bar("2024-02-29 09:30")}), "leap timestamp accepted");
        require(p.get_data(0).timestamp == "2024-02-29T09:30:00" && p.get_data(0).minute_of_day == 570, "normalization");
    });
    test("strict parser rejects invalid prices and allows zero volume", [] {
        CSVParser p;
        auto b = bar("2025-01-01"); b.open = std::numeric_limits<double>::quiet_NaN(); require(!p.load_records({b}), "NaN");
        b = bar("2025-01-01"); b.high = 90; require(!p.load_records({b}), "OHLC");
        b = bar("2025-01-01"); b.volume = -1; require(!p.load_records({b}), "negative volume");
        b = bar("2025-01-01"); b.volume = 0; require(p.load_records({b}), "zero volume accepted");
    });
    test("CSV parsing reports numeric and row errors", [] {
        const auto path = std::filesystem::temp_directory_path() / "trading_bot_engine_validation.csv";
        auto load = [&](const std::string& text) {
            { std::ofstream f(path); f << text; }
            CSVParser p; const bool ok = p.load_data(path.string());
            std::filesystem::remove(path);
            return ok;
        };
        require(!load("date,open,high,low,close,volume\n2025-01-01,100oops,100,100,100,1\n"), "trailing numeric garbage");
        require(!load("date,open,high,low,close,volume\n2025-01-01,100,100,100,100\n"), "missing columns");
        require(!load("date,open,high,low,close,volume\n2025-01-01,100,100,100,100,1,extra\n"), "extra columns");
        require(!load("invalid,header\n"), "header");
        require(!load("date,open,high,low,close,volume\n2025-01-01,nan,100,100,100,1\n"), "nonfinite numeric");
        require(load("Date,Open,High,Low,Close,Volume\n2025-01-01,100,100,100,100,0\n"), "legacy case header accepted");
    });
    test("event history independently reconciles cash and quantities", [] {
        auto c = config(); c.commission_fixed = 1;
        auto r = run({bar("2025-01-01"), bar("2025-01-02"), bar("2025-01-03", 110, 110)},
                     {signal(SignalType::BUY, 10), signal(SignalType::SELL, 4)}, c);
        Cents cash = 0; std::int64_t quantity = 0;
        for (const auto& e : r.events) {
            cash += e.cash_delta_cents;
            if (e.type == "BUY") quantity += e.quantity;
            if (e.type == "SELL") quantity -= e.quantity;
            require(cash == e.cash_after_cents && quantity == e.quantity_after, "event balance");
        }
        require(cash == r.final_cash_cents && quantity == r.final_quantity, "final reconciliation");
    });
    std::cout << passed << " engine cases passed\n";
    return 0;
} catch (const std::exception& error) {
    std::cerr << "FAIL " << error.what() << '\n'; return 1;
}
}

