#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace TradingBot {
// Money is signed 64-bit cents; all external amounts round half away from zero.
using Cents = std::int64_t;
Cents to_cents(double amount);
double from_cents(Cents amount);
Cents checked_add(Cents left, Cents right);
Cents checked_multiply(Cents amount, std::int64_t quantity);

struct LedgerEvent {
    std::string event_id, timestamp, type;
    std::int64_t quantity = 0, quantity_after = 0;
    Cents price_cents = 0, fee_cents = 0, cash_delta_cents = 0, cash_after_cents = 0;
    Cents cost_basis_after_cents = 0, realized_pnl_after_cents = 0;
    double price = 0, fee = 0, cash_delta = 0, cash_after = 0, cost_basis_after = 0, realized_pnl_after = 0;
};
class Ledger {
public:
    explicit Ledger(double initial_cash, const std::string& timestamp = "INITIAL");
    LedgerEvent buy(std::int64_t quantity, double price, double fee, const std::string& timestamp);
    LedgerEvent sell(std::int64_t quantity, double price, double fee, const std::string& timestamp);
    LedgerEvent buy_cents(std::int64_t quantity, Cents price, Cents fee, const std::string& timestamp);
    LedgerEvent sell_cents(std::int64_t quantity, Cents price, Cents fee, const std::string& timestamp);
    void mark(double price, const std::string& timestamp);
    Cents cash_cents() const { return cash_; }
    Cents cost_basis_cents() const { return basis_; }
    Cents realized_pnl_cents() const { return realized_; }
    Cents fees_cents() const { return fees_; }
    std::int64_t quantity() const { return quantity_; }
    Cents market_value_cents(double price) const;
    Cents equity_cents(double price) const;
    Cents unrealized_pnl_cents(double price) const;
    double average_cost() const;
    const std::vector<LedgerEvent>& events() const { return events_; }
private:
    Cents cash_ = 0, basis_ = 0, realized_ = 0, fees_ = 0;
    std::int64_t quantity_ = 0;
    std::vector<LedgerEvent> events_;
    LedgerEvent make_event(const std::string& type, const std::string& timestamp,
                           std::int64_t quantity, Cents price, Cents fee, Cents delta,
                           Cents cash, std::int64_t held, Cents basis, Cents realized) const;
};
}

