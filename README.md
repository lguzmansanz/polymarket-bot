# Polymarket Bot v1 (paper trading)

MVP para descubrir mercados líquidos, leer precios reales de Polymarket, generar señales simples y simular operaciones con un ledger auditable.

## Qué hace
- Usa el SDK oficial `polymarket-client`.
- Descubre mercados abiertos.
- Lee midpoint, spread y último precio del token YES.
- Mantiene una ventana temporal de precios por mercado.
- Genera señales de momentum muy conservadoras como punto de partida.
- Simula compras/ventas en PAPER con slippage configurable.
- Registra cada snapshot, señal, operación y P&L en SQLite.
- Incluye límites duros de riesgo y kill switch.

## Qué NO hace todavía
- No envía órdenes reales.
- No copia wallets automáticamente.
- No usa un LLM para tomar decisiones financieras.
- No promete rentabilidad.

## Requisitos
Python 3.11+

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Por defecto empieza con 1.000 USDC virtuales y escanea cada 15 segundos.

## Ajustes
Edita `.env`:
- `PAPER_STARTING_CASH`
- `SCAN_SECONDS`
- `MAX_MARKETS`
- `MAX_POSITION_PCT`
- `MAX_DAILY_LOSS_PCT`
- `MOMENTUM_WINDOW`
- `ENTRY_MOVE`
- `EXIT_MOVE`
- `PAPER_SLIPPAGE_BPS`

La fase siguiente debería añadir: histórico/backtest, ranking de wallets, señales de flujo, evaluación out-of-sample y solo después un ejecutor real separado.
