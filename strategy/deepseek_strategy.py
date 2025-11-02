"""
DeepSeek AI Strategy for NautilusTrader

AI-powered cryptocurrency trading strategy using DeepSeek for decision making,
technical indicators for market analysis, and sentiment data for validation.
"""

import os
from decimal import Decimal
from typing import Dict, Any, Optional, List, Tuple

from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce, PositionSide, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.position import Position
from nautilus_trader.model.orders import MarketOrder
from datetime import timedelta

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.technical_manager import TechnicalIndicatorManager
from utils.deepseek_client import DeepSeekAnalyzer
from utils.sentiment_client import SentimentDataFetcher


class DeepSeekAIStrategyConfig(StrategyConfig, frozen=True):
    """Configuration for DeepSeek AI Strategy."""

    # Instrument
    instrument_id: str
    bar_type: str

    # Capital
    equity: float = 10000.0
    leverage: float = 10.0

    # Position sizing
    base_usdt_amount: float = 100.0
    high_confidence_multiplier: float = 1.5
    medium_confidence_multiplier: float = 1.0
    low_confidence_multiplier: float = 0.5
    max_position_ratio: float = 0.10
    trend_strength_multiplier: float = 1.2
    min_trade_amount: float = 0.001

    # Technical indicators
    sma_periods: Tuple[int, ...] = (5, 20, 50)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    bb_period: int = 20
    bb_std: float = 2.0

    # AI configuration
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_temperature: float = 0.1
    deepseek_max_retries: int = 2

    # Sentiment
    sentiment_enabled: bool = True
    sentiment_lookback_hours: int = 4
    sentiment_timeframe: str = "15m"  # Sentiment data timeframe (should match or be compatible with bar_type)

    # Risk management
    min_confidence_to_trade: str = "MEDIUM"
    allow_reversals: bool = True
    require_high_confidence_for_reversal: bool = False
    rsi_extreme_threshold_upper: float = 75.0
    rsi_extreme_threshold_lower: float = 25.0
    rsi_extreme_multiplier: float = 0.7

    # Execution
    position_adjustment_threshold: float = 0.001

    # Timing
    timer_interval_sec: int = 900


class DeepSeekAIStrategy(Strategy):
    """
    DeepSeek AI-powered trading strategy.

    Combines AI decision making, technical analysis, and sentiment data
    for intelligent cryptocurrency trading on Binance Futures.
    """

    def __init__(self, config: DeepSeekAIStrategyConfig):
        """
        Initialize DeepSeek AI strategy.

        Parameters
        ----------
        config : DeepSeekAIStrategyConfig
            Strategy configuration
        """
        super().__init__(config)

        # Configuration
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Position sizing config
        self.equity = config.equity
        self.leverage = config.leverage
        self.base_usdt = config.base_usdt_amount
        self.position_config = {
            'high_confidence_multiplier': config.high_confidence_multiplier,
            'medium_confidence_multiplier': config.medium_confidence_multiplier,
            'low_confidence_multiplier': config.low_confidence_multiplier,
            'max_position_ratio': config.max_position_ratio,
            'trend_strength_multiplier': config.trend_strength_multiplier,
            'min_trade_amount': config.min_trade_amount,
            'adjustment_threshold': config.position_adjustment_threshold,
        }

        # Risk management
        self.min_confidence = config.min_confidence_to_trade
        self.allow_reversals = config.allow_reversals
        self.require_high_conf_reversal = config.require_high_confidence_for_reversal
        self.rsi_extreme_upper = config.rsi_extreme_threshold_upper
        self.rsi_extreme_lower = config.rsi_extreme_threshold_lower
        self.rsi_extreme_mult = config.rsi_extreme_multiplier

        # Technical indicators manager
        sma_periods = config.sma_periods if config.sma_periods else [5, 20, 50]
        self.indicator_manager = TechnicalIndicatorManager(
            sma_periods=sma_periods,
            ema_periods=[config.macd_fast, config.macd_slow],
            rsi_period=config.rsi_period,
            macd_fast=config.macd_fast,
            macd_slow=config.macd_slow,
            bb_period=config.bb_period,
            bb_std=config.bb_std,
        )

        # DeepSeek AI analyzer
        api_key = config.deepseek_api_key or os.getenv('DEEPSEEK_API_KEY')
        if not api_key:
            raise ValueError("DeepSeek API key not provided")

        self.deepseek = DeepSeekAnalyzer(
            api_key=api_key,
            model=config.deepseek_model,
            temperature=config.deepseek_temperature,
            max_retries=config.deepseek_max_retries,
        )

        # Sentiment data fetcher
        self.sentiment_enabled = config.sentiment_enabled
        if self.sentiment_enabled:
            # Use sentiment_timeframe from config, or derive from bar_type if not specified
            sentiment_tf = config.sentiment_timeframe
            if not sentiment_tf or sentiment_tf == "":
                # Extract timeframe from bar_type (e.g., "1-MINUTE" -> "1m")
                bar_str = str(self.bar_type)
                if "1-MINUTE" in bar_str:
                    sentiment_tf = "1m"
                elif "5-MINUTE" in bar_str:
                    sentiment_tf = "5m"
                elif "15-MINUTE" in bar_str:
                    sentiment_tf = "15m"
                elif "1-HOUR" in bar_str:
                    sentiment_tf = "1h"
                else:
                    sentiment_tf = "15m"  # Default fallback
            
            self.sentiment_fetcher = SentimentDataFetcher(
                lookback_hours=config.sentiment_lookback_hours,
                timeframe=sentiment_tf,
            )
            self.log.info(f"Sentiment fetcher initialized with timeframe: {sentiment_tf}")
        else:
            self.sentiment_fetcher = None

        # State tracking
        self.instrument: Optional[Instrument] = None
        self.last_signal: Optional[Dict[str, Any]] = None
        self.bars_received = 0

        self.log.info(f"DeepSeek AI Strategy initialized for {self.instrument_id}")

    def on_start(self):
        """Actions to be performed on strategy start."""
        self.log.info("Starting DeepSeek AI Strategy...")

        # Load instrument
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument {self.instrument_id}")
            self.stop()
            return

        self.log.info(f"Loaded instrument: {self.instrument.id}")

        # Subscribe to bars
        self.subscribe_bars(self.bar_type)
        self.log.info(f"Subscribed to {self.bar_type}")

        # Set up timer for periodic analysis
        self.clock.set_timer(
            name="analysis_timer",
            interval=timedelta(seconds=self.config.timer_interval_sec),
            callback=self.on_timer,
        )

        self.log.info("Strategy started successfully")

    def on_stop(self):
        """Actions to be performed on strategy stop."""
        self.log.info("Stopping DeepSeek AI Strategy...")

        # Cancel any pending orders
        self.cancel_all_orders(self.instrument_id)

        # Unsubscribe from data
        self.unsubscribe_bars(self.bar_type)

        self.log.info("Strategy stopped")

    def on_bar(self, bar: Bar):
        """
        Handle bar updates.

        Parameters
        ----------
        bar : Bar
            The bar received
        """
        self.bars_received += 1

        # Update technical indicators
        self.indicator_manager.update(bar)

        # Log bar data
        if self.bars_received % 10 == 0:
            self.log.info(
                f"Bar #{self.bars_received}: "
                f"O:{bar.open} H:{bar.high} L:{bar.low} C:{bar.close} V:{bar.volume}"
            )

    def on_timer(self, event):
        """
        Periodic analysis and trading logic.

        Called every timer_interval_sec seconds (default: 15 minutes).
        """
        self.log.info("=" * 60)
        self.log.info("Running periodic analysis...")

        # Check if indicators are ready
        if not self.indicator_manager.is_initialized():
            self.log.warning("Indicators not yet initialized, skipping analysis")
            return

        # Get current market data
        current_bar = self.indicator_manager.recent_bars[-1] if self.indicator_manager.recent_bars else None
        if not current_bar:
            self.log.warning("No bars available for analysis")
            return

        current_price = float(current_bar.close)

        # Get technical data
        try:
            technical_data = self.indicator_manager.get_technical_data(current_price)
            self.log.debug(f"Technical data retrieved: {len(technical_data)} indicators")
        except Exception as e:
            self.log.error(f"Failed to get technical data: {e}")
            return

        # Get K-line data
        kline_data = self.indicator_manager.get_kline_data(count=10)
        self.log.debug(f"Retrieved {len(kline_data)} K-lines for analysis")

        # Get sentiment data
        sentiment_data = None
        if self.sentiment_enabled and self.sentiment_fetcher:
            try:
                sentiment_data = self.sentiment_fetcher.fetch()
                if sentiment_data:
                    self.log.info(self.sentiment_fetcher.format_for_display(sentiment_data))
            except Exception as e:
                self.log.warning(f"Failed to fetch sentiment data: {e}")

        # Build price data for AI
        price_data = {
            'price': current_price,
            'timestamp': self.clock.utc_now().isoformat(),
            'high': float(current_bar.high),
            'low': float(current_bar.low),
            'volume': float(current_bar.volume),
            'price_change': self._calculate_price_change(),
            'kline_data': kline_data,
        }

        # Get current position
        current_position = self._get_current_position_data()

        # Log current state
        self.log.info(f"Current Price: ${current_price:,.2f}")
        self.log.info(f"Overall Trend: {technical_data.get('overall_trend', 'N/A')}")
        self.log.info(f"RSI: {technical_data.get('rsi', 0):.2f}")
        if current_position:
            self.log.info(
                f"Current Position: {current_position['side']} "
                f"{current_position['quantity']} @ ${current_position['avg_px']:.2f}"
            )

        # Analyze with DeepSeek AI
        try:
            self.log.info("Calling DeepSeek AI for analysis...")
            signal_data = self.deepseek.analyze(
                price_data=price_data,
                technical_data=technical_data,
                sentiment_data=sentiment_data,
                current_position=current_position,
            )
            self.log.info(
                f"🤖 Signal: {signal_data['signal']} | "
                f"Confidence: {signal_data['confidence']} | "
                f"Reason: {signal_data['reason']}"
            )
        except Exception as e:
            self.log.error(f"DeepSeek AI analysis failed: {e}", exc_info=True)
            return

        # Store signal
        self.last_signal = signal_data

        # Execute trade
        self._execute_trade(signal_data, price_data, technical_data, current_position)

    def _calculate_price_change(self) -> float:
        """Calculate price change percentage."""
        bars = self.indicator_manager.recent_bars
        if len(bars) < 2:
            return 0.0

        current = float(bars[-1].close)
        previous = float(bars[-2].close)

        return ((current - previous) / previous) * 100

    def _get_current_position_data(self) -> Optional[Dict[str, Any]]:
        """Get current position information."""
        # Get open positions for this instrument
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        
        if not positions:
            return None
        
        # Get the first open position (should only be one for netting OMS)
        position = positions[0]
        
        if position and position.is_open:
            # Get current price for PnL calculation
            # Use last bar close price as it's more reliable than cache.price()
            # cache.price() requires tick data which may not be available
            bars = self.indicator_manager.recent_bars
            if bars:
                current_price = bars[-1].close
            else:
                # Fallback: try cache.price() if bars not available
                try:
                    current_price = self.cache.price(self.instrument_id, PriceType.LAST)
                except (TypeError, AttributeError):
                    current_price = None
            
            return {
                'side': 'long' if position.side == PositionSide.LONG else 'short',
                'quantity': float(position.quantity),
                'avg_px': float(position.avg_px_open),
                'unrealized_pnl': float(position.unrealized_pnl(current_price)) if current_price else 0.0,
            }

        return None

    def _execute_trade(
        self,
        signal_data: Dict[str, Any],
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]],
    ):
        """
        Execute trading logic based on signal.

        Parameters
        ----------
        signal_data : Dict
            AI-generated signal
        price_data : Dict
            Current price data
        technical_data : Dict
            Technical indicators
        current_position : Dict or None
            Current position info
        """
        signal = signal_data['signal']
        confidence = signal_data['confidence']

        # Check minimum confidence
        confidence_levels = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
        min_conf_level = confidence_levels.get(self.min_confidence, 1)
        signal_conf_level = confidence_levels.get(confidence, 1)

        if signal_conf_level < min_conf_level:
            self.log.warning(
                f"⚠️ Signal confidence {confidence} below minimum {self.min_confidence}, skipping trade"
            )
            return

        # Handle HOLD signal
        if signal == 'HOLD':
            self.log.info("📊 Signal: HOLD - No action taken")
            return

        # Calculate target position size
        target_quantity = self._calculate_position_size(
            signal_data, price_data, technical_data, current_position
        )

        if target_quantity == 0:
            self.log.warning("⚠️ Calculated position size is 0, skipping trade")
            return

        # Determine order side
        target_side = 'long' if signal == 'BUY' else 'short'

        # Execute position management logic
        if current_position:
            self._manage_existing_position(
                current_position, target_side, target_quantity, confidence
            )
        else:
            self._open_new_position(target_side, target_quantity)

    def _calculate_position_size(
        self,
        signal_data: Dict[str, Any],
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]],
    ) -> float:
        """
        Calculate intelligent position size.

        Returns BTC quantity based on confidence, trend, and RSI.
        """
        # Base USDT amount
        base_usdt = self.base_usdt

        # Confidence multiplier
        conf_mult = self.position_config.get(
            f"{signal_data['confidence'].lower()}_confidence_multiplier",
            1.0
        )

        # Trend multiplier
        trend = technical_data.get('overall_trend', '震荡整理')
        trend_mult = (
            self.position_config['trend_strength_multiplier']
            if trend in ['强势上涨', '强势下跌']
            else 1.0
        )

        # RSI multiplier (reduce size in extreme RSI)
        rsi = technical_data.get('rsi', 50)
        rsi_mult = (
            self.rsi_extreme_mult
            if rsi > self.rsi_extreme_upper or rsi < self.rsi_extreme_lower
            else 1.0
        )

        # Calculate suggested USDT
        suggested_usdt = base_usdt * conf_mult * trend_mult * rsi_mult

        # Apply max position ratio limit
        max_usdt = self.equity * self.position_config['max_position_ratio']
        final_usdt = min(suggested_usdt, max_usdt)

        # Convert to BTC quantity
        current_price = price_data['price']
        btc_quantity = final_usdt / current_price

        # Apply minimum trade amount
        if btc_quantity < self.position_config['min_trade_amount']:
            btc_quantity = self.position_config['min_trade_amount']

        # Round to instrument precision
        btc_quantity = round(btc_quantity, 3)  # Binance BTC precision

        self.log.info(
            f"📊 Position Sizing: "
            f"Base:{base_usdt} × Conf:{conf_mult} × Trend:{trend_mult} × RSI:{rsi_mult} "
            f"= ${final_usdt:.2f} = {btc_quantity:.3f} BTC"
        )

        return btc_quantity

    def _manage_existing_position(
        self,
        current_position: Dict[str, Any],
        target_side: str,
        target_quantity: float,
        confidence: str,
    ):
        """Manage existing position (add, reduce, or reverse)."""
        current_side = current_position['side']
        current_qty = current_position['quantity']

        # Same direction - adjust position
        if target_side == current_side:
            size_diff = target_quantity - current_qty
            threshold = self.position_config['adjustment_threshold']

            if abs(size_diff) < threshold:
                self.log.info(
                    f"✅ Position size appropriate ({current_qty:.3f} BTC), no adjustment needed"
                )
                return

            if size_diff > 0:
                # Add to position
                self._submit_order(
                    side=OrderSide.BUY if target_side == 'long' else OrderSide.SELL,
                    quantity=abs(size_diff),
                    reduce_only=False,
                )
                self.log.info(
                    f"📈 Adding to {target_side} position: {abs(size_diff):.3f} BTC "
                    f"({current_qty:.3f} → {target_quantity:.3f})"
                )
            else:
                # Reduce position
                self._submit_order(
                    side=OrderSide.SELL if target_side == 'long' else OrderSide.BUY,
                    quantity=abs(size_diff),
                    reduce_only=True,
                )
                self.log.info(
                    f"📉 Reducing {target_side} position: {abs(size_diff):.3f} BTC "
                    f"({current_qty:.3f} → {target_quantity:.3f})"
                )

        # Opposite direction - reverse position
        elif self.allow_reversals:
            # Check if high confidence required for reversal
            if self.require_high_conf_reversal and confidence != 'HIGH':
                self.log.warning(
                    f"🔒 Reversal requires HIGH confidence, got {confidence}. "
                    f"Keeping {current_side} position."
                )
                return

            self.log.info(f"🔄 Reversing position: {current_side} → {target_side}")

            # Close current position
            self._submit_order(
                side=OrderSide.SELL if current_side == 'long' else OrderSide.BUY,
                quantity=current_qty,
                reduce_only=True,
            )

            # Open opposite position
            self._submit_order(
                side=OrderSide.BUY if target_side == 'long' else OrderSide.SELL,
                quantity=target_quantity,
                reduce_only=False,
            )

        else:
            self.log.warning(
                f"⚠️ Signal suggests {target_side} but have {current_side} position. "
                f"Reversals disabled."
            )

    def _open_new_position(self, side: str, quantity: float):
        """Open new position."""
        order_side = OrderSide.BUY if side == 'long' else OrderSide.SELL

        self._submit_order(
            side=order_side,
            quantity=quantity,
            reduce_only=False,
        )

        self.log.info(f"🚀 Opening {side} position: {quantity:.3f} BTC")

    def _submit_order(
        self,
        side: OrderSide,
        quantity: float,
        reduce_only: bool = False,
    ):
        """Submit market order to exchange."""
        if quantity < self.position_config['min_trade_amount']:
            self.log.warning(
                f"⚠️ Order quantity {quantity:.3f} below minimum "
                f"{self.position_config['min_trade_amount']:.3f}, skipping"
            )
            return

        # Create market order
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(quantity),
            time_in_force=TimeInForce.GTC,
            reduce_only=reduce_only,
        )

        # Submit order
        self.submit_order(order)

        self.log.info(
            f"📤 Submitted {side.name} market order: {quantity:.3f} BTC "
            f"(reduce_only={reduce_only})"
        )

    def on_order_filled(self, event):
        """Handle order filled events."""
        self.log.info(
            f"✅ Order filled: {event.order_side.name} "
            f"{event.last_qty} @ {event.last_px}"
        )

    def on_order_rejected(self, event):
        """Handle order rejected events."""
        self.log.error(f"❌ Order rejected: {event.reason}")

    def on_position_opened(self, event):
        """Handle position opened events."""
        position = event.position
        self.log.info(
            f"🟢 Position opened: {position.side.name} "
            f"{position.quantity} @ {position.avg_px_open}"
        )

    def on_position_closed(self, event):
        """Handle position closed events."""
        position = event.position
        self.log.info(
            f"🔴 Position closed: {position.side.name} "
            f"P&L: {position.realized_pnl:.2f} USDT"
        )
