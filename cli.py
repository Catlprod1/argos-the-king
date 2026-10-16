from __future__ import annotations

import argparse
import json

from .approvals import ApprovalQueue
from .audit import AuditLog
from .briefing import BriefingService
from .config import Config
from .models import Action
from .market import BybitPublicMarket, MarketDataError, append_ticker, latest_ticker
from .market_watch import watch
from .notifier import TelegramNotifier
from .runtime_watch import timer_state
from .notifier import owner_notifier
from .paper import PaperError, PaperLedger
from .service import ArgosService
from .training import TrainingRegister
from .telegram_commands import TelegramCommands
from .whalebot import FeedError, collect
from .onion_observer import OnionObserverError, poll_onions
from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="argos")
    parser.add_argument("--config", default="config/argos.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("brief")
    sub.add_parser("notify-brief")
    sub.add_parser("telegram-poll", help="Respond to owner-only Telegram commands")
    sub.add_parser("collect-whalebot", help="Sample public WhaleBot posts without API keys")
    sub.add_parser("watch-market", help="Check closed mainnet spot candles and alert owner")
    sub.add_parser("poll-crypto-observations", help="Read six fixed onion pages through local Tor")
    proposed = sub.add_parser("propose")
    proposed.add_argument("kind")
    proposed.add_argument("--target", default="")
    proposed.add_argument("--account", default="")
    proposed.add_argument("--payload", default="{}")
    approvals = sub.add_parser("approvals")
    approvals.add_argument("--approve")
    approvals.add_argument("--reject")
    market = sub.add_parser("collect-market", help="Record one public Testnet spot ticker")
    market.add_argument("symbol")
    paper_open = sub.add_parser("paper-open", help="Open a simulated spot-long position")
    paper_open.add_argument("symbol")
    paper_open.add_argument("--stop", required=True)
    paper_open.add_argument("--risk-usdt", required=True)
    paper_open.add_argument("--thesis", required=True)
    paper_close = sub.add_parser("paper-close", help="Close a simulated spot-long position")
    paper_close.add_argument("trade_id")
    paper_close.add_argument("symbol")
    sub.add_parser("paper-summary")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = Config.load(args.config)
    service = ArgosService(config)
    paper_settings = config.section("paper")
    ledger = PaperLedger(
        config.data_directory / "paper.jsonl",
        equity_usdt=str(paper_settings.get("equity_usdt", "1000")),
        max_risk_usdt=str(paper_settings.get("max_risk_usdt", "5")),
        max_total_open_risk_usdt=str(paper_settings.get("max_total_open_risk_usdt", "20")),
        fee_rate=str(paper_settings.get("fee_rate", "0.001")),
        slippage_rate=str(paper_settings.get("slippage_rate", "0.001")),
    )
    snapshots = config.data_directory / "market.jsonl"

    if args.command == "status":
        print(json.dumps({
            "name": "Argos",
            "version": __version__,
            "data_directory": str(config.data_directory),
            "pending_approvals": len(service.approvals.pending()),
            "trading_enabled": config.section("trading").get("enabled", False),
            "market_watch_timer": timer_state(),
        }, indent=2))
    elif args.command == "brief":
        briefing = BriefingService(
            config,
            AuditLog(config.data_directory / "audit.jsonl"),
            ApprovalQueue(config.data_directory / "approvals.json"),
            TrainingRegister(config.data_directory / "training.json"),
        )
        print(briefing.render())
    elif args.command == "notify-brief":
        briefing = BriefingService(
            config,
            AuditLog(config.data_directory / "audit.jsonl"),
            ApprovalQueue(config.data_directory / "approvals.json"),
            TrainingRegister(config.data_directory / "training.json"),
        )
        result = owner_notifier(config).send(briefing.render())
        service.audit.append("owner_notification", {
            "channel": result.channel,
            "delivered": result.delivered,
            "detail": result.detail,
        })
    elif args.command == "telegram-poll":
        TelegramCommands(config).poll_once()
    elif args.command == "collect-whalebot":
        try:
            count = collect(config.data_directory / "whalebot.json")
            print(f"Recorded {count} new public WhaleBot posts")
        except FeedError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.command == "watch-market":
        try:
            count = watch(config.section("trading").get("market_universe", []),
                          config.data_directory / "market-watch.json",
                          TelegramNotifier.from_config(config))
            print(f"Sent {count} market research alerts")
        except (ValueError, RuntimeError) as exc:
            raise SystemExit(str(exc)) from exc
    elif args.command == "poll-crypto-observations":
        try:
            count = poll_onions(config, owner_notifier(config))
            print(f"Recorded {count} new public crypto identifiers")
        except OnionObserverError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.command == "propose":
        action = Action(
            kind=args.kind,
            target=args.target,
            account=args.account,
            payload=json.loads(args.payload),
        )
        print(json.dumps(service.propose(action).to_dict(), indent=2))
    elif args.command == "approvals":
        if args.approve:
            print("approved" if service.approvals.decide(args.approve, True) else "not found")
        elif args.reject:
            print("rejected" if service.approvals.decide(args.reject, False) else "not found")
        else:
            print(json.dumps(service.approvals.pending(), indent=2))
    elif args.command == "collect-market":
        try:
            ticker = BybitPublicMarket(config.section("trading").get("market_universe", [])).ticker(args.symbol)
            append_ticker(snapshots, ticker)
            print(json.dumps(ticker.__dict__, indent=2))
        except MarketDataError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.command == "paper-open":
        try:
            ticker = latest_ticker(snapshots, args.symbol)
            print(json.dumps(ledger.open_long(ticker, args.stop, args.thesis, args.risk_usdt), indent=2))
        except (MarketDataError, PaperError) as exc:
            raise SystemExit(str(exc)) from exc
    elif args.command == "paper-close":
        try:
            ticker = latest_ticker(snapshots, args.symbol)
            print(json.dumps(ledger.close_long(args.trade_id, ticker), indent=2))
        except (MarketDataError, PaperError) as exc:
            raise SystemExit(str(exc)) from exc
    elif args.command == "paper-summary":
        print(json.dumps(ledger.summary(), indent=2))


if __name__ == "__main__":
    main()
