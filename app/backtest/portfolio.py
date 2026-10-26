"""Portefeuille simulé : mêmes règles que l'API (exposition par position, vente de toute la
position, stop-loss et take-profit), frais et glissement compris.

Hypothèses (écrites ici parce qu'elles changent le résultat) :
- un ordre décidé à la clôture d'une bougie s'exécute à l'**ouverture de la suivante** (jamais au
  prix qui a déclenché le signal) ;
- les frais s'appliquent à chaque ordre, et le glissement dégrade chaque prix d'exécution ;
- stop-loss et take-profit sont contrôlés sur chaque bougie ; si les deux sont touchés dans la même
  bougie, le stop-loss l'emporte (hypothèse prudente : on ne sait pas lequel est venu en premier) ;
  si la bougie ouvre déjà au-delà du niveau, la sortie se fait à l'ouverture (écart de prix, pas de
  prix magique).
"""

from dataclasses import dataclass, field

MIN_NOTIONAL = 10.0  # valeur minimale d'un ordre (minimum de Binance, en USDT)


@dataclass
class Position:
    quantity: float
    avg_price: float
    entry_index: int
    stop_loss: float
    take_profit: float
    #: Frais déjà payés à l'entrée, rattachés au résultat de la sortie.
    entry_fees: float = 0.0


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float
    quantity: float
    #: Gain net des frais (entrée et sortie), en USDT.
    pnl: float
    #: « SELL » (signal), « STOP_LOSS » ou « TAKE_PROFIT ».
    reason: str


@dataclass
class Portfolio:
    cash: float
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005
    max_exposure_pct: float = 5.0
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 6.0
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    fees_paid: float = 0.0

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.quantity * prices[s] for s, p in self.positions.items())

    def invested(self, prices: dict[str, float]) -> float:
        return sum(p.quantity * prices[s] for s, p in self.positions.items())

    def buy(
        self, symbol: str, score: float, price: float, index: int, prices: dict[str, float]
    ) -> bool:
        """Achat dimensionné comme l'API : la place restante sous le plafond d'exposition,
        proportionnelle au score."""
        equity = self.equity(prices)
        held_value = self.positions[symbol].quantity * price if symbol in self.positions else 0.0
        room = equity * self.max_exposure_pct / 100 - held_value
        if room <= 0:
            return False
        fill = price * (1 + self.slippage_rate)
        spend = min(
            room * score, self.cash / (1 + self.fee_rate)
        )  # jamais plus que le cash disponible
        if spend < MIN_NOTIONAL:
            return False
        quantity = spend / fill
        fee = spend * self.fee_rate
        self.cash -= spend + fee
        self.fees_paid += fee

        previous = self.positions.get(symbol)
        total = quantity + (previous.quantity if previous else 0.0)
        avg = (
            quantity * fill + (previous.quantity * previous.avg_price if previous else 0.0)
        ) / total
        self.positions[symbol] = Position(
            quantity=total,
            avg_price=avg,
            entry_index=previous.entry_index if previous else index,
            stop_loss=avg * (1 - self.stop_loss_pct / 100),
            take_profit=avg * (1 + self.take_profit_pct / 100),
            entry_fees=(previous.entry_fees if previous else 0.0) + fee,
        )
        return True

    def sell_all(self, symbol: str, price: float, index: int, reason: str) -> bool:
        """Vend toute la position, au prix `price` dégradé par le glissement, frais déduits."""
        position = self.positions.pop(symbol, None)
        if position is None:
            return False
        fill = price * (1 - self.slippage_rate)
        proceeds = position.quantity * fill
        fee = proceeds * self.fee_rate
        self.cash += proceeds - fee
        self.fees_paid += fee
        cost = position.quantity * position.avg_price
        self.trades.append(
            Trade(
                symbol=symbol,
                entry_index=position.entry_index,
                exit_index=index,
                entry_price=position.avg_price,
                exit_price=fill,
                quantity=position.quantity,
                pnl=proceeds - fee - cost - position.entry_fees,
                reason=reason,
            )
        )
        return True

    def check_exits(self, index: int, candle: dict[str, dict[str, float]]) -> None:
        """Stop-loss et take-profit sur la bougie `index` (`candle[symbole]` : open, high, low)."""
        for symbol in list(self.positions):
            position = self.positions[symbol]
            bar = candle[symbol]
            if bar["open"] <= position.stop_loss:
                self.sell_all(symbol, bar["open"], index, "STOP_LOSS")  # ouverture sous le niveau
            elif bar["low"] <= position.stop_loss:
                self.sell_all(symbol, position.stop_loss, index, "STOP_LOSS")
            elif bar["open"] >= position.take_profit:
                self.sell_all(symbol, bar["open"], index, "TAKE_PROFIT")
            elif bar["high"] >= position.take_profit:
                self.sell_all(symbol, position.take_profit, index, "TAKE_PROFIT")
