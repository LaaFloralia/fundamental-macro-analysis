"""Public-source adapters for USOIL. Each adapter returns a normalised SourceResult.

Adopted oil-source inventory: no Coinbase, Twelve
Data, CoinGlass or paid CME data; OHLC comes from Yahoo (market), same-day
quotes and FRED series from CNBC/FRED (macro), positioning from CFTC COT
(cot), inventories from EIA WPSR (eia), volatility from the Cboe OVX quote
(vol) and the events from the official calendars plus ForexFactory (calendar).
Raw bodies are never returned; only normalised values and ``raw_sha256``.
"""
