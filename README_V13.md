# Polymarket V1.3 — solo PAPER

La V1.3 usa exclusivamente GET de las APIs públicas Gamma/CLOB. No carga .env, no pide claves, no firma transacciones y no contiene un ejecutor real. bot.py y bot_v12.py se conservan.

## Ejecutar en el Codespace

Python 3.11+ y Linux (bloqueo fcntl). La dependencia de esta versión es httpx; queda declarada de forma independiente.

~~~bash
.venv/bin/python -m pip install -r requirements-v13.txt
.venv/bin/python -m unittest -v test_v13
.venv/bin/python bot_v13.py --minutes 5
.venv/bin/python report_v13.py --output v13_metrics.json
~~~

Omitir --minutes mantiene el proceso hasta Ctrl+C. Al parar se conservan posiciones; no se simula una venta sin liquidez. El siguiente arranque retoma la misma base. Para un experimento independiente usar --db otro_experimento_v13.sqlite3. Nunca reutilizar las bases de versiones anteriores. No se puede cambiar Config sobre una base existente. No se leen overrides del .env de V1/V1.2.

## Selección

- Fechas end_date_min=ahora+6 horas, end_date_max=ahora+14 días, comprobadas también localmente.
- Hasta tres páginas de 100 mercados, ordenadas por volumen24hr descendente; no es un barrido de todo Polymarket.
- Volumen de 24h mínimo 1.000 y liquidez mínima 10.000; datos ausentes/NaN se rechazan.
- Exclusión de familias Up or Down/updown/5m/15m, incluso publicadas con antelación. EventStartTime/gameStartTime, cuando existen, también deben quedar a más de seis horas.
- Cambio 1h de al menos 0,003 puntos de probabilidad; alternativa 1d de al menos 0,01, con controles de contradicción. Compra YES o NO según dirección. Sin soporte para mercados no binarios Yes/No.
- Confirmación con dos cotizaciones separadas entre 5 y 180 segundos sin reversión local >0,001. No exige de nuevo una subida de 0,004 en solo 8 sondeos.
- Se revisan los 15 candidatos de mayor score. Precio ask entre 0,10 y 0,85; spread absoluto máximo 0,02 y relativo 3,5%. Libro reciente, no cruzado y profundidad suficiente.

## Riesgo y simulación

Límites: 2% máximo por posición, 2 posiciones, 4% de exposición como techo adicional, una posición por evento y kill switch diario UTC del 2%. El presupuesto disponible del día limita además el capital total que podría perderse íntegramente. Se reparte entre las plazas restantes: inicialmente alrededor de 10 por plaza sobre 1.000, por debajo del techo por posición. Nunca se calcula el tamaño suponiendo que el stop será garantizado.

Las posiciones se gestionan antes de descubrir mercados, incluso si han desaparecido del ranking o cotizan cerca de cero. Stop -6% y take-profit +12% sobre valor neto de liquidación; salida a dos horas del endDate. Un fallo de datos o una salida pendiente bloquea nuevas entradas. El kill queda enclavado hasta el siguiente día UTC y se guarda en SQLite. Si se activa, intenta liquidar usando bids disponibles. Una orden parcial permanece pendiente; no se inventa liquidez. Una resolución final de Gamma, con estado resolved y precios binarios 0/1, permite liquidación contable sin libro. No se confunde closed con resolved.

Compra al ask y venta al bid recorriendo profundidad. Slippage adicional 15 bps; comisión de escenario 200 bps por lado, NO una reproducción exacta de la tarifa de cada mercado ni una cota universal. La entrada requiere profundidad para ambas direcciones y coste estimado de ida/vuelta menor o igual al 6%. No hay garantía de fill real ni de ejecución al nivel del stop. Una cotización ausente mantiene la última marca, queda señalada como stale y bloquea compras; esa equity puede estar sobrevalorada.

La base paper_trading_v13.sqlite3 guarda estado, configuración, operaciones, candidatos y equity. Cada operación y su estado se confirman juntos. Un bloqueo del sistema impide procesos simultáneos con la misma base. Reintentos limitados para TransportError, 408/429/5xx con backoff y jitter. Otros errores HTTP no se reintentan. Los libros con marca temporal de más de 120 segundos se descartan. El intervalo nominal es 15 s; las peticiones y reintentos pueden alargar un ciclo.

## Observabilidad y métricas

paper_v13.log rota a 5 MB con tres copias. La consola muestra contadores de cada filtro; SQLite conserva datos y motivos por candidato, entrada y salida. La base puede crecer en ejecuciones largas; conservar/archivar experimentos terminados antes de iniciar otros.

report_v13.py distingue fills (compras/ventas parciales), entradas y posiciones totalmente cerradas. Win rate usa solo posiciones cerradas; sin cierres devuelve null. Incluye P&L realizado/no realizado/total, comisiones simuladas, equity final, muestras stale y drawdown aproximado calculado sobre equity muestreada. Los costes de salida estimados ya se descuentan al marcar posiciones abiertas. Una prueba de minutos comprueba funcionamiento y no demuestra rentabilidad.

## Diagnóstico de versiones anteriores

La base de V1.1 confirma compra de Bitcoin Up or Down 3:50PM–3:55PM ET el 8 de septiembre a las 19:53:11 UTC a 0,2553825, por 50. Venta a las 19:53:26 a 0,0948575, motivo momentum reversal -0.2000: pérdida 31,42835 (62,86% de la posición; 3,14% de la cuenta). Entró en un contrato de 5 minutos con menos de dos minutos restantes. El salto entre sondeos superó ampliamente el stop nominal. Además, las versiones antiguas omitían precios extremos antes de gestionar riesgos, gestionaban solo mercados aún seleccionados y reiniciaban su estado en memoria al arrancar. La reversión se evaluaba antes del stop; por eso el motivo registrado fue reversión.

La V1.2 incorpora seis horas de margen, pero solo usa first_page() ordenada por endDate. En la reproducción con defaults del 10 de septiembre a las 03:03 UTC llegaron 100 mercados pese a solicitar 200; 81 fallaron liquidez y 19 volumen. Quedaron cero candidatos. Además, el movimiento 1h solo afecta al ranking; la entrada sigue exigiendo una subida local de 0,004 durante ocho observaciones. No había logging que distinguiera un universo vacío de falta de señal. La base V1.2 contiene también operaciones posteriores: no se afirma que nunca operara. diagnose_v12.py reproduce el selector sin ejecutar broker ni cargar .env; la muestra actual no prueba qué filtros exactos fallaron en cada ciclo histórico.

Fuentes públicas de esquema: https://docs.polymarket.com/api-reference/markets/list-markets y https://docs.polymarket.com/api-reference/market-data/get-order-book .
