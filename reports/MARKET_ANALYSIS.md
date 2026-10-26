# Анализ рынка Bitget USDT-M (4H)

Сгенерировано: 2026-10-26T12:56:00+00:00. История: до 1460 дней. Комиссия 0.06% + проскальзывание 0.05% на сторону, funding учтён. Риск 1.0% капитала на сделку в каждом слоте, плечо слота ≤ 2×.

Все цифры ниже — **out-of-sample** (walk-forward: параметры подбираются на 1 году, проверяются на следующих 3 месяцах, которых бот не видел).

## Выбранный портфель

| Монета | Стратегия | Параметры (последнее окно) | OOS доходность | OOS CAGR | Макс. просадка | Sharpe | Сделок | Плюсовых окон |
|---|---|---|---|---|---|---|---|---|
| ENAUSDT | tsmom | `{"lookback": 126, "atr_stop": 3.0, "allow_short": true}` | 15.4% | 12.3% | 7.4% | 1.35 | 54 | 60.0% |
| BTCUSDT | tsmom | `{"lookback": 252, "atr_stop": 5.0, "allow_short": true}` | 24.7% | 8.5% | 5.4% | 1.23 | 90 | 90.9% |
| ETHUSDT | donchian | `{"entry_n": 100, "exit_n": 10, "atr_stop": 2.5, "allow_short": true}` | 25.3% | 8.7% | 7.8% | 1.18 | 64 | 63.6% |
| SUIUSDT | tsmom | `{"lookback": 126, "atr_stop": 3.0, "allow_short": true}` | 20.8% | 8.9% | 7.7% | 1.06 | 81 | 77.8% |
| XRPUSDT | tsmom (через auto) | `{"lookback": 42, "atr_stop": 3.0, "allow_short": true}` | 16.1% | 5.7% | 8.4% | 0.74 | 94 | 54.5% |

**Портфель целиком (OOS 2024-02-04 → 2026-10-26):** доходность 144.6%, CAGR 38.8%, макс. просадка 17.6%, Sharpe 1.58; BTC buy&hold за тот же период: 87.3%.

## Контрольная проверка на отложенных данных

Портфель выбран **только по данным до 2026-04-29**, затем проверен на последних 180 днях: слоты ENAUSDT:donchian, ETHUSDT:tsmom, BTCUSDT:tsmom, SUIUSDT:ema_trend, PEPEUSDT:ema_trend; доходность 5.2%, макс. просадка 19.4%, Sharpe 0.49; BTC buy&hold: 3.7%. Это ближе всего к тому, что можно ожидать в реальности.

Фильтры отбора: сделок ≥ 10, OOS доходность > 0, Sharpe ≥ 0.5, просадка ≤ 35.0%, OOS-окон ≥ 4, плюсовых окон ≥ 50.0%, корреляция со уже выбранными ≤ 0.6, одна стратегия на монету.

## Топ-25 пар (монета × стратегия) по OOS Sharpe

| # | Монета | Стратегия | OOS доходность | Просадка | Sharpe | Сделок | Плюсовых окон | Статус |
|---|---|---|---|---|---|---|---|---|
| 1 | DOODUSDT | supertrend | 6.0% | 4.3% | 2.18 | 9 | 100.0% | few trades, short history |
| 2 | HYPEUSDT | tsmom | 9.5% | 4.3% | 1.62 | 22 | 66.7% | short history |
| 3 | ENAUSDT | tsmom | 15.4% | 7.4% | 1.35 | 54 | 60.0% | ✔ проходит |
| 4 | ENAUSDT | donchian | 14.1% | 6.5% | 1.32 | 51 | 60.0% | ✔ проходит |
| 5 | BTCUSDT | tsmom | 24.7% | 5.4% | 1.23 | 90 | 90.9% | ✔ проходит |
| 6 | HYPEUSDT | auto | 8.6% | 7.5% | 1.22 | 25 | 66.7% | short history |
| 7 | DOODUSDT | tsmom | 3.1% | 5.6% | 1.22 | 6 | 100.0% | few trades, short history |
| 8 | ETHUSDT | donchian | 25.3% | 7.8% | 1.18 | 64 | 63.6% | ✔ проходит |
| 9 | HYPEUSDT | supertrend | 8.1% | 7.5% | 1.16 | 28 | 66.7% | short history |
| 10 | HYPEUSDT | donchian | 7.0% | 5.0% | 1.13 | 20 | 66.7% | short history |
| 11 | ENAUSDT | ema_trend | 12.0% | 6.1% | 1.13 | 38 | 60.0% | ✔ проходит |
| 12 | ENAUSDT | auto | 12.9% | 7.4% | 1.12 | 53 | 60.0% | ✔ проходит |
| 13 | SUIUSDT | tsmom | 20.8% | 7.7% | 1.06 | 81 | 77.8% | ✔ проходит |
| 14 | ENAUSDT | supertrend | 15.2% | 9.8% | 1.01 | 62 | 20.0% | unstable folds |
| 15 | ETHUSDT | tsmom | 19.5% | 9.6% | 0.92 | 74 | 63.6% | ✔ проходит |
| 16 | BTCUSDT | donchian | 18.2% | 11.1% | 0.83 | 94 | 54.5% | ✔ проходит |
| 17 | BTCUSDT | auto | 15.6% | 7.5% | 0.79 | 94 | 72.7% | ✔ проходит |
| 18 | XRPUSDT | auto | 16.1% | 8.4% | 0.74 | 94 | 54.5% | ✔ проходит |
| 19 | ETHUSDT | auto | 13.6% | 8.6% | 0.71 | 62 | 72.7% | ✔ проходит |
| 20 | SUIUSDT | ema_trend | 11.7% | 7.0% | 0.71 | 60 | 66.7% | ✔ проходит |
| 21 | BTCUSDT | supertrend | 17.7% | 12.5% | 0.69 | 138 | 54.5% | ✔ проходит |
| 22 | XRPUSDT | ema_trend | 10.2% | 5.0% | 0.67 | 34 | 36.4% | unstable folds |
| 23 | SUIUSDT | mean_reversion | 3.8% | 2.7% | 0.66 | 11 | 55.6% | ✔ проходит |
| 24 | DOODUSDT | donchian | 1.0% | 2.5% | 0.64 | 3 | 100.0% | few trades, short history |
| 25 | SOLUSDT | mean_reversion | 0.6% | 0.2% | 0.56 | 1 | 9.1% | few trades, unstable folds |

## Какая стратегия где работает (OOS Sharpe)

| Монета | donchian | ema_trend | supertrend | tsmom | mean_reversion | auto |
|---|---|---|---|---|---|---|
| BTCUSDT | 0.83 | 0.39 | 0.69 | 1.23 | -0.39 | 0.79 |
| ETHUSDT | 1.18 | 0.09 | -0.08 | 0.92 | -0.23 | 0.71 |
| SOLUSDT | 0.31 | 0.05 | -0.02 | 0.38 | 0.56 | -0.31 |
| XRPUSDT | 0.39 | 0.67 | 0.01 | 0.50 | 0.24 | 0.74 |
| NEARUSDT | 0.40 | 0.19 | -0.19 | 0.05 | -0.72 | -0.04 |
| ENAUSDT | 1.32 | 1.13 | 1.01 | 1.35 | -0.32 | 1.12 |
| DOODUSDT | 0.64 | 0.14 | 2.18 | 1.22 | -1.69 | -1.69 |
| HYPEUSDT | 1.13 | -0.14 | 1.16 | 1.62 | 0.00 | 1.22 |
| PEPEUSDT | 0.54 | 0.33 | 0.05 | 0.45 | -0.26 | 0.31 |
| SUIUSDT | 0.06 | 0.71 | 0.22 | 1.06 | 0.66 | 0.33 |

## Характер рынков

| Монета | Объём 24ч, $млн | Лет истории | Волатильность/год | Средний ADX | Efficiency ratio | Тренд сейчас (EMA200) | ADX сейчас | Доходность 1г | Средний funding/8ч |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 2090 | 4.0 | 45.1% | 28.4 | 0.174 | down | 37.5 | -29.3% | 0.0055% |
| ETHUSDT | 1341 | 4.0 | 61.0% | 27.6 | 0.173 | down | 36.5 | -42.7% | 0.0061% |
| SOLUSDT | 137 | 4.0 | 89.7% | 27.8 | 0.171 | down | 27.7 | -49.0% | 0.0044% |
| XRPUSDT | 95 | 4.0 | 76.6% | 27.1 | 0.155 | down | 23.8 | -51.0% | 0.0069% |
| NEARUSDT | 74 | 4.0 | 101.8% | 27.4 | 0.166 | down | 26.4 | 94.2% | 0.0072% |
| ENAUSDT | 42 | 2.56 | 129.0% | 27.9 | 0.172 | up | 15.9 | -58.7% | 0.0037% |
| DOODUSDT | 33 | 1.47 | 151.9% | 27.7 | 0.162 | up | 32.7 | -70.0% | -0.0034% |
| HYPEUSDT | 33 | 1.85 | 108.6% | 25.3 | 0.153 | down | 29.0 | 63.4% | 0.0026% |
| PEPEUSDT | 31 | 3.48 | 129.7% | 27.4 | 0.169 | up | 16.1 | -40.3% | 0.0039% |
| SUIUSDT | 25 | 3.48 | 104.4% | 26.9 | 0.162 | down | 37.7 | -65.2% | 0.0061% |

## Стратегии

- **donchian** — Trend: breakout of the N-bar channel in the direction of EMA200, exit on the opposite M-bar channel, ATR trailing stop.
- **ema_trend** — Trend: fast/slow EMA alignment, entries only when ADX confirms a trend, ATR trailing stop.
- **supertrend** — Trend: Supertrend direction, the stop rides the Supertrend line.
- **tsmom** — Momentum: sign of the N-bar return confirmed by price vs its N-bar average, wide ATR stop.
- **mean_reversion** — Range: Bollinger band + RSI extremes, only while ADX says there is no trend; exit at the mean, time stop.
- **auto** — на каждом окне обучения выбирает лучшую стратегию и параметры из всех перечисленных.

> Прошлые результаты не гарантируют будущих. Отбор лучших пар из многих вариантов завышает OOS-цифры (эффект множественного тестирования) — поэтому смотрите на контрольную проверку и начинайте с бумажной торговли.
