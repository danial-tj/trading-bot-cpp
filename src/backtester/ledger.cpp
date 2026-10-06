#include "backtester/ledger.h"
#include <charconv>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace TradingBot {
namespace {
// Exact round(a*b/d), a>=0 and 0<=b<=d, without a potentially overflowing a*b.
Cents proportional_cost(Cents a, std::int64_t b, std::int64_t d) {
    using U = std::uint64_t;
    const U denominator = static_cast<U>(d), aq = static_cast<U>(a) / denominator;
    const U ar = static_cast<U>(a) % denominator;
    U quotient = 0, remainder = 0;
    for (int bit = 62; bit >= 0; --bit) {
        quotient *= 2;
        remainder *= 2; // Each remainder is <d<=INT64_MAX, so uint64 cannot overflow.
        if (remainder >= denominator) { remainder -= denominator; ++quotient; }
        if ((static_cast<U>(b) >> bit) & 1U) {
            quotient += aq;
            remainder += ar;
            if (remainder >= denominator) { remainder -= denominator; ++quotient; }
        }
    }
    if (remainder >= denominator - remainder) ++quotient;
    if (quotient > static_cast<U>(std::numeric_limits<Cents>::max())) throw std::overflow_error("cost allocation overflow");
    return static_cast<Cents>(quotient);
}
}
Cents to_cents(double amount) {
    if (!std::isfinite(amount)) throw std::invalid_argument("money must be finite");
    // Quantize the shortest decimal representation of the double. Multiplying a
    // binary double by 100 would incorrectly send decimal ties such as 1.005 down.
    char buffer[64];
    const auto converted = std::to_chars(buffer, buffer + sizeof(buffer), amount);
    if (converted.ec != std::errc{}) throw std::overflow_error("cannot represent monetary amount");
    const std::string value(buffer, converted.ptr);
    const bool negative = value.front() == '-';
    const auto exponent_at = value.find_first_of("eE");
    const auto end = exponent_at == std::string::npos ? value.size() : exponent_at;
    const int exponent = exponent_at == std::string::npos ? 0 : std::stoi(value.substr(exponent_at + 1));
    std::string digits;
    int fractional_digits = 0;
    bool decimal = false;
    for (size_t i = negative ? 1 : 0; i < end; ++i) {
        if (value[i] == '.') decimal = true;
        else { digits += value[i]; if (decimal) ++fractional_digits; }
    }
    const int keep = static_cast<int>(digits.size()) + exponent - fractional_digits + 2;
    using U = std::uint64_t;
    const U limit = static_cast<U>(std::numeric_limits<Cents>::max()) + (negative ? 1ULL : 0ULL);
    U magnitude = 0;
    for (int i = 0; i < keep; ++i) {
        const U digit = i < static_cast<int>(digits.size()) ? static_cast<U>(digits[static_cast<size_t>(i)] - '0') : 0;
        if (magnitude > (limit - digit) / 10) throw std::overflow_error("money exceeds signed 64-bit cents");
        magnitude = magnitude * 10 + digit;
    }
    if (keep >= 0 && keep < static_cast<int>(digits.size()) && digits[static_cast<size_t>(keep)] >= '5') {
        if (magnitude == limit) throw std::overflow_error("cent rounding overflow");
        ++magnitude;
    }
    if (negative && magnitude == static_cast<U>(std::numeric_limits<Cents>::max()) + 1ULL)
        return std::numeric_limits<Cents>::min();
    return negative ? -static_cast<Cents>(magnitude) : static_cast<Cents>(magnitude);
}
double from_cents(Cents amount) { return static_cast<double>(amount) / 100.0; }
Cents checked_add(Cents left, Cents right) {
    if ((right > 0 && left > std::numeric_limits<Cents>::max() - right) ||
        (right < 0 && left < std::numeric_limits<Cents>::min() - right))
        throw std::overflow_error("money addition overflow");
    return left + right;
}
Cents checked_multiply(Cents amount, std::int64_t quantity) {
    if (amount < 0 || quantity < 0) throw std::invalid_argument("monetary products require nonnegative factors");
    if (quantity && amount > std::numeric_limits<Cents>::max() / quantity)
        throw std::overflow_error("money multiplication overflow");
    return amount * quantity;
}
LedgerEvent Ledger::make_event(const std::string& type, const std::string& timestamp,
                              std::int64_t quantity, Cents price, Cents fee, Cents delta,
                              Cents cash, std::int64_t held, Cents basis, Cents realized) const {
    LedgerEvent e;
    e.event_id = "event-" + std::to_string(events_.size() + 1);
    e.type = type; e.timestamp = timestamp; e.quantity = quantity; e.quantity_after = held;
    e.price_cents = price; e.fee_cents = fee; e.cash_delta_cents = delta; e.cash_after_cents = cash;
    e.cost_basis_after_cents = basis; e.realized_pnl_after_cents = realized;
    e.price = from_cents(price); e.fee = from_cents(fee); e.cash_delta = from_cents(delta);
    e.cash_after = from_cents(cash); e.cost_basis_after = from_cents(basis); e.realized_pnl_after = from_cents(realized);
    return e;
}
Ledger::Ledger(double initial_cash, const std::string& timestamp) {
    cash_ = to_cents(initial_cash);
    if (cash_ <= 0) throw std::invalid_argument("initial cash must be at least one cent");
    events_.push_back(make_event("DEPOSIT", timestamp, 0, 0, 0, cash_, cash_, 0, 0, 0));
}
LedgerEvent Ledger::buy(std::int64_t quantity, double price, double fee, const std::string& timestamp) {
    if (fee < 0) throw std::invalid_argument("buy fee cannot be negative");
    return buy_cents(quantity, to_cents(price), to_cents(fee), timestamp);
}
LedgerEvent Ledger::buy_cents(std::int64_t quantity, Cents p, Cents f, const std::string& timestamp) {
    if (quantity <= 0 || p <= 0 || f < 0) throw std::invalid_argument("invalid buy quantity, price or fee");
    const auto cost = checked_add(checked_multiply(p, quantity), f);
    if (cost > cash_) throw std::invalid_argument("insufficient cash");
    const auto next_quantity = checked_add(quantity_, quantity);
    const auto next_basis = checked_add(basis_, cost), next_fees = checked_add(fees_, f);
    const auto event = make_event("BUY", timestamp, quantity, p, f, -cost, cash_ - cost, next_quantity, next_basis, realized_);
    // Append before state mutation: allocation failure cannot leave a partial fill.
    events_.push_back(event);
    quantity_ = next_quantity; basis_ = next_basis; cash_ -= cost; fees_ = next_fees;
    return event;
}
LedgerEvent Ledger::sell(std::int64_t quantity, double price, double fee, const std::string& timestamp) {
    if (fee < 0) throw std::invalid_argument("sale fee cannot be negative");
    return sell_cents(quantity, to_cents(price), to_cents(fee), timestamp);
}
LedgerEvent Ledger::sell_cents(std::int64_t quantity, Cents p, Cents f, const std::string& timestamp) {
    if (quantity <= 0 || p <= 0 || f < 0) throw std::invalid_argument("invalid sale quantity, price or fee");
    if (quantity > quantity_) throw std::invalid_argument("sale exceeds owned quantity; short execution disabled");
    const auto proceeds = checked_add(checked_multiply(p, quantity), -f);
    const auto next_cash = checked_add(cash_, proceeds);
    if (next_cash < 0) throw std::invalid_argument("cash cannot cover sale fee");
    // Preserve the last cent on final disposal; split proportional cost for partial exits.
    const auto allocation = quantity == quantity_ ? basis_ : proportional_cost(basis_, quantity, quantity_);
    const auto next_realized = checked_add(realized_, checked_add(proceeds, -allocation));
    const auto next_fees = checked_add(fees_, f);
    const auto event = make_event("SELL", timestamp, quantity, p, f, proceeds, next_cash, quantity_ - quantity, basis_ - allocation, next_realized);
    events_.push_back(event);
    cash_ = next_cash; quantity_ -= quantity; basis_ -= allocation; realized_ = next_realized; fees_ = next_fees;
    return event;
}
void Ledger::mark(double price, const std::string& timestamp) {
    const auto p = to_cents(price);
    if (p <= 0) throw std::invalid_argument("mark must be positive after cent rounding");
    (void)equity_cents(price);
    events_.push_back(make_event("MARK", timestamp, 0, p, 0, 0, cash_, quantity_, basis_, realized_));
}
Cents Ledger::market_value_cents(double price) const {
    const auto p = to_cents(price);
    if (p <= 0) throw std::invalid_argument("mark must be positive after cent rounding");
    return checked_multiply(p, quantity_);
}
Cents Ledger::equity_cents(double price) const { return checked_add(cash_, market_value_cents(price)); }
Cents Ledger::unrealized_pnl_cents(double price) const { return checked_add(market_value_cents(price), -basis_); }
double Ledger::average_cost() const { return quantity_ ? from_cents(basis_) / static_cast<double>(quantity_) : 0; }
}

