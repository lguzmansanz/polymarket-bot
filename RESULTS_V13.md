# Resultado V1.3 — PAPER ONLY

Prueba en Codespace effective-space-waddle, del 2026-09-10T03:00:47.579362+00:00 al 2026-09-10T03:06:47.929372+00:00.

Implementación aplicada directamente en /workspaces/polymarket-bot. Sin órdenes reales ni claves privadas.

## Verificación

- Sintaxis de bot.py, bot_v12.py, bot_v13.py, report_v13.py, diagnose_v12.py y test_v13.py: correcta.
- 22 pruebas automatizadas: correctas. Cubren fechas, momentum YES/NO, profundidad, costes, stop en precios extremos, take-profit, salida previa al vencimiento, kill y cambio UTC, persistencia, salidas parciales, liquidación confirmada y reintentos acotados.
- Segunda ejecución simultánea: rechazada con Another V1.3 process owns this database.
- Dos ejecuciones de aproximadamente tres minutos; segunda sobre la misma base, conservando la posición y el capital.
- Dependencia usada: httpx 0.28.1. Linux/Python del entorno .venv del Codespace.

- v13_smoke_console.log: 2026-09-10 03:03:47,753 INFO STOPPED cycles=12 errors=0 elapsed=180.2 state_preserved=True
- v13_final_smoke.log: 2026-09-10 03:07:00,964 INFO STOPPED cycles=12 errors=0 elapsed=180.1 state_preserved=True

## Métricas acumuladas

| Métrica | Resultado |
|---|---|
| Entradas / fills totales | 1 / 1 |
| Posiciones cerradas | 0 |
| Win rate | No calculable: no hay cierres |
| P&L realizado | 0.000000 |
| P&L no realizado | -0.841874 |
| P&L total | -0.841874 |
| Equity inicial / final | 1000.00 / 999.158126 |
| Máximo drawdown aproximado | 1.067264 (0.1067%) |
| Posiciones abiertas | 1 |
| Muestras de equity / marcadas stale | 26 / 1 |

Las marcas descuentan spread, profundidad, slippage y coste de salida estimado. La comisión de 200 bps por lado es una hipótesis de simulación, no la tarifa real de cada mercado. Un P&L inicial ligeramente negativo tras entrar refleja estos costes.

La primera entrada se creó antes del ajuste final que reparte el presupuesto diario entre plazas: usó 20 de capital ficticio. Ese estado se conservó en la segunda prueba; no se reinició el saldo para mejorar resultados. La versión final reparte inicialmente alrededor de 10 por plaza y mantiene el techo del 2% por posición.

## Diagnóstico

V1.1: el registro histórico confirma -31,42835 sobre 50 en un contrato Bitcoin de cinco minutos. El precio pasó de compra 0,2553825 a venta 0,0948575 entre sondeos separados por unos 15 segundos. El motivo de salida fue momentum reversal -0.2000. No hubo protección garantizada contra el salto.

V1.2, muestra actual con defaults: 100 mercados recibidos; 0 tras filtros. Descartes: {'liquidity': 81, 'volume24h': 19}. Solo first_page ordenada por endDate y señal de ocho observaciones explican la escasez de entradas. No se extrapola esta muestra a todos los ciclos históricos; su base ya contiene 22 fills.

V1.3: 300 mercados por ciclo en las pruebas, con aproximadamente 55–62 candidatos elegibles antes del filtro de libro. Los demás motivos quedan desglosados en v13_metrics.json.

## Estado final y límites

Proceso de prueba parado limpiamente; posiciones abiertas conservadas en SQLite, sin liquidación ficticia forzada. Para continuar usar .venv/bin/python bot_v13.py. Para consultar resultados usar .venv/bin/python report_v13.py.

El resultado de minutos no prueba rentabilidad. No es un backtest ni una comparación simultánea ajustada por costes contra V1.2. Las fechas de resolución pueden cambiar; los eventos que resuelven antes de su endDate y la liquidez ausente siguen siendo riesgos. El sondeo y los reintentos pueden alargar el intervalo nominal de 15 s.

## Posición conservada

~~~json
[
  {
    "market": "4319476",
    "question": "Will Bitcoin dip to $74,000 September 7-13?",
    "outcome": "No",
    "opened": "2026-09-10T03:01:03.911186+00:00",
    "end": "2026-09-14T04:00:00+00:00",
    "qty": 23.033500498963203,
    "cost": 19.999999999999996,
    "mark": 19.158125556762897
  }
]
~~~

## Operaciones observadas

~~~json
[
  [
    "2026-09-10T03:01:03.911206+00:00",
    "BUY",
    "4319476",
    23.033500498963203,
    0.851275,
    0.392156862745098,
    0.0,
    "recent_momentum_confirmed"
  ]
]
~~~
