using System;
using System.Runtime.Serialization;
using System.Text.Json;

using cAlgo.API;
using cAlgo.API.Indicators;
using Phy.Lib;

namespace Phy.Bot
{
    [Robot(AccessRights = AccessRights.None)]
    public class PhyBot : Robot, IBotEngine
    {
        private string _label = "PhyBot_Signal";
        private int _barsHeld = 0;
        private int _currSpread = 0;
        private T_SIG _tSig;
        private IndData _indData;
        private PhysicsEngine _engine;
        //private CSignal _signal;
        private CStrategies _strategy;
        private CStats _stats;
        private CAppState _appState;
        private CUtils _utils;
        private bool _canTradeThisBar = false;
        // Results objects for indicators
        private StandardDeviation _stdClose;
        private StandardDeviation _stdOpen;
        private MoneyFlowIndex _mfi;
        private AverageTrueRange _atr;
        private AverageDirectionalMovementIndexRating _adx;
        private OnBalanceVolume _obv;
        private RelativeStrengthIndex _rsi;
        private SimpleMovingAverage _ima5;
        private SimpleMovingAverage _ima14;
        private SimpleMovingAverage _ima30;
        private SimpleMovingAverage _ima60;
        private SimpleMovingAverage _ima120;
        private SimpleMovingAverage _ima240;
        private SimpleMovingAverage _ima500;
        private SimpleMovingAverage _avgStdDev;


        public double[] GetHistory(Func<int, double> getPricePtr, int count, int shift = 1)
        {
            double[] buffer = new double[count];
            int startPoint = Bars.Count - 1 - shift;

            for (int i = 0; i < count; i++)
            {
                int targetIndex = startPoint - i;
                if (targetIndex >= 0)
                {
                    double val = getPricePtr(targetIndex);

                    // If the indicator is still "warming up", default to 0.0
                    buffer[i] = double.IsNaN(val) ? 0.0 : val;
                }
                else
                {
                    buffer[i] = 0.0;
                }
            }
            return buffer;
        }
        // FIX: Added 'void' return type to match the interface contract
        public void InitIndData()
        {


            _indData = new IndData

            {
                MagicNumber = 1002,
                BarsHeld = 0,
                Open = GetHistory(idx => Bars.OpenPrices[idx], 500),
                High = GetHistory(idx => Bars.HighPrices[idx], 120),
                Low = GetHistory(idx => Bars.LowPrices[idx], 120),
                Close = GetHistory(idx => Bars.ClosePrices[idx], 500),
                Time = GetHistory(idx => Bars.OpenTimes[idx].ToOADate(), 500).Select(t => DateTime.FromOADate(t)).ToArray(),
                TickVolume = GetHistory(idx => Bars.TickVolumes[idx], 500),
                StdClose = GetHistory(idx => _stdClose.Result[idx], 500),
                StdOpen = GetHistory(idx => _stdOpen.Result[idx], 500),
                Mfi = GetHistory(idx => _mfi.Result[idx], 500),
                Atr = GetHistory(idx => _atr.Result[idx], 500),
                Adx = GetHistory(idx => _adx.ADXR[idx], 500),     // main line = ADXR (smoothed ADX)
                AdxPlus = GetHistory(idx => _adx.DIPlus[idx], 500),   // +DI
                AdxMinus = GetHistory(idx => _adx.DIMinus[idx], 500),
                Obv = GetHistory(idx => _obv.Result[idx], 500),
                Rsi = GetHistory(idx => _rsi.Result[idx], 500), // -DI
                Ima5 = GetHistory(idx => _ima5.Result[idx], 500),
                Ima14 = GetHistory(idx => _ima14.Result[idx], 500),
                Ima30 = GetHistory(idx => _ima30.Result[idx], 500),
                Ima60 = GetHistory(idx => _ima60.Result[idx], 500),
                Ima120 = GetHistory(idx => _ima120.Result[idx], 500),
                Ima240 = GetHistory(idx => _ima240.Result[idx], 500),
                Ima500 = GetHistory(idx => _ima500.Result[idx], 500),
                AvgStd = GetHistory(idx => _avgStdDev.Result[idx], 500),
                Point = Symbol.TickSize,
                Digits = Symbol.Digits,
                PipValue = Symbol.PipValue,
                PipSize = Symbol.PipSize,
                DBL_EPSILON = (Symbol.TickSize * 0.1),
                // cTrader .NET 6 standard way to get total minutes
                _Period = GetMinutes(TimeFrame),
                Current_Period = GetMinutes(TimeFrame),
                Shift = 0,
                TotalOrders = Positions.Count(p => p.Label == _label && p.SymbolName == SymbolName)
            };
        }

        // Manual extraction for .NET 6 cTrader API
        public int GetMinutes(TimeFrame tf)
        {
            if (tf == TimeFrame.Minute) return 1;
            if (tf == TimeFrame.Minute5) return 5;
            if (tf == TimeFrame.Minute15) return 15;
            if (tf == TimeFrame.Hour) return 60;
            if (tf == TimeFrame.Daily) return 1440;
            // Fallback: parse the name (e.g., "m15" -> 15)
            var match = System.Text.RegularExpressions.Regex.Match(tf.ToString(), @"\d+");
            return match.Success ? int.Parse(match.Value) : 1;
        }
        public void printData(in IndData data)
        {
            Print("=== IndData Snapshot ===");
            Print($"MagicNumber: {data.MagicNumber}");
            Print($"Open: {data.Open[0]}");
            Print($"Close: {data.Close[0]}");
            Print($"StdClose: {data.StdClose[0]}");
            Print($"StdOpen: {data.StdOpen[0]}");
            Print($"Mfi: {data.Mfi[0]}");
            Print($"Atr: {data.Atr[0]}");
            Print($"Adx: {data.Adx[0]}");
            Print($"AdxPlus: {data.AdxPlus[0]}");
            Print($"AdxMinus: {data.AdxMinus[0]}");
            Print($"Obv: {data.Obv[0]}");
            Print($"Rsi: {data.Rsi[0]}");
            Print($"Ima5: {data.Ima5[0]}");
            Print($"Ima14: {data.Ima14[0]}");
            Print($"Ima30: {data.Ima30[0]}");
            Print($"Ima60: {data.Ima60[0]}");
            Print($"Ima120: {data.Ima120[0]}");
            Print($"Ima240: {data.Ima240[0]}");
            Print($"Ima500: {data.Ima500[0]}");
            Print($"TradePosition: {data.TradePosition}");
            Print($"CurrSpread: {data.CurrSpread}");
            Print($"Shift: {data.Shift}");
            Print($"BarsHeld: {data.BarsHeld}");
            Print($"HoldScore: {data.HoldScore}");

            // Optionally print the latest values of the historical arrays
            if (data.Close.Length > 0)
                Print($"Latest Close Price: {data.Close[0]}");
            if (data.StdClose.Length > 0)
                Print($"Latest StdDev of Close: {data.StdClose[0]}");
            if (data.Mfi.Length > 0)
                Print($"Latest MFI: {data.Mfi[0]}");
        }

        public bool HasTradedCurrentBarIncludingHistory(ulong magicNumber)
        {
            DateTime currentBarStartTime = Bars.OpenTimes.LastValue;
            string label = magicNumber.ToString();

            // Check active positions
            bool activeExists = Positions.Any(p => p.Label == label && p.EntryTime >= currentBarStartTime);
            if (activeExists) return true;

            // Check closed positions in history
            bool historyExists = History.Any(h => h.Label == label && h.EntryTime >= currentBarStartTime);

            return historyExists;
        }

        private void SyncSubsystems(IndData data)
        {
            _appState.SetIndData(data);
            _stats.SetIndData(data);
            _utils.SetIndData(data);
            _engine.SetIndData(data);
        }

        protected override void OnStart()
        {
            try
            {

                Print("Phy.Bot initialized. Connecting to Physics Engine...");
                _stdClose = Indicators.StandardDeviation(Bars.ClosePrices, 20, MovingAverageType.Simple);
                _stdOpen = Indicators.StandardDeviation(Bars.OpenPrices, 20, MovingAverageType.Simple);
                _mfi = Indicators.MoneyFlowIndex(20);
                _atr = Indicators.AverageTrueRange(20, MovingAverageType.Simple);
                _adx = Indicators.AverageDirectionalMovementIndexRating(20);
                _obv = Indicators.OnBalanceVolume(Bars.ClosePrices);
                _rsi = Indicators.RelativeStrengthIndex(Bars.ClosePrices, 14);
                _ima5 = Indicators.SimpleMovingAverage(Bars.ClosePrices, 5);
                _ima14 = Indicators.SimpleMovingAverage(Bars.ClosePrices, 14);
                _ima30 = Indicators.SimpleMovingAverage(Bars.ClosePrices, 30);
                _ima60 = Indicators.SimpleMovingAverage(Bars.ClosePrices, 60);
                _ima120 = Indicators.SimpleMovingAverage(Bars.ClosePrices, 120);
                _ima240 = Indicators.SimpleMovingAverage(Bars.ClosePrices, 240);
                _ima500 = Indicators.SimpleMovingAverage(Bars.ClosePrices, 500);
                _avgStdDev = Indicators.SimpleMovingAverage(_stdClose.Result, 40);

                InitIndData();
                // 3. Instantiate Subsystems (Check if _indData is valid)
                if (_indData == null) throw new InvalidOperationException("Failed to initialize IndData.");

                _utils = new CUtils(_indData);
                _stats = new CStats(_indData, _utils);
                _appState = new CAppState(_indData, _utils);
                _engine = new PhysicsEngine(_indData, _stats, _utils, _appState);
                _engine.Log = this.Print;
                //_signal = new CSignal(_engine, _stats, _utils);
                _strategy = new CStrategies(_engine, _stats, _utils);
                //_tSig = _signal.InitSignal();
                _tSig = _strategy.getSignal().InitSignal();
                Print("Phy.Bot initialized successfully.");

            }
            catch (Exception ex)
            {
                Print("CRITICAL ERROR during OnStart: {0}", ex.Message);
                Stop(); // Stop the bot if initialization fails to prevent OnBar crashes
            }

            // Inject cTrader's Print method for logging
        }



        protected override void OnTick()
        {

        }

        protected override void OnBar()
        {

            if (_engine == null || _strategy == null || _indData == null || _stats == null || _utils == null || _appState == null)
            {
                Print("OnBar skipped: Subsystems not initialized.");
                return;
            }
            // A new random comment.
            try
            {
                InitIndData();
                _barsHeld = getMaxBarAge();
                _currSpread = (int)Math.Ceiling((Symbol.Spread / Symbol.PipSize));
                this._indData = this._indData with
                {
                    CurrSpread = _currSpread,
                    SpreadLimit = (int)_engine.atrScale(15, 120),
                    BarsHeld = _barsHeld,
                    CandleTraded = HasTradedCurrentBarIncludingHistory(this._indData.MagicNumber)
                };

                this._indData = _engine.ProcessMarketData(this._indData); // Reset shift for the new bar

                SyncSubsystems(this._indData);
                // Update app state with the latest data
                this._canTradeThisBar = true;
                // _tSig = _signal.InitSecSignal(this._canTradeThisBar);
                _tSig = _strategy.getSignal().InitSignal();


                onBarTask1();



            }
            catch (NullReferenceException nre)
            {
                Print("NullRef in OnBar! Check if an indicator result is NaN or if a subsystem is null: {0}", nre.StackTrace);
            }
            catch (Exception ex)
            {
                Print("Error in OnBar: {0}", ex.Message);
            }

        }

        protected override void OnStop()
        {
            Print("Cleaning up resources...");
            // TO SAVE: Turn the object into a string
            string jsonSaveFile = JsonSerializer.Serialize(_appState);

            // TO LOAD: Turn the string back into an object
            var myState = JsonSerializer.Deserialize<CAppState>(jsonSaveFile);
        }



        private int GetBarAge(Position position)
        {
            // 1. Get the index of the bar when the position was opened
            int entryBarIndex = Bars.OpenTimes.GetIndexByTime(position.EntryTime);

            // 2. The current bar index is always the last one in the series
            int currentBarIndex = Bars.Count - 1;

            // 3. The difference is the number of bars the trade has existed
            return currentBarIndex - entryBarIndex;
        }

        private int getMaxBarAge()
        {

            int maxAge = 0;
            var botPositions = Positions.FindAll(_label, SymbolName);
            foreach (var pos in botPositions)
            {
                int age = GetBarAge(pos);
                if (age > maxAge)
                    maxAge = age;
            }
            return maxAge;
        }

        private int GetActivePositionsCount(string label)
        {
            // 'Positions' is the collection of all currently open trades in the account.
            // We filter them by Label (the MagicNumber equivalent) and Symbol.
            return Positions.Count(p => p.Label == label && p.SymbolName == SymbolName);

        }
  
        public double GetAverageTradeProfit(Positions positions, ulong magicNumber)
        {
            double totalProfit = 0.0;
            int tradeCount = 0;


            // Convert ulong magic number to a string label (cTrader's tracking method)
            string labelFilter = magicNumber.ToString();

            // 1. Loop through active running positions
            foreach (var position in positions)
            {
                // 2. Match by Symbol Name and Magic Number Label
                if (position.SymbolName == SymbolName && position.Label == labelFilter)
                {
                    // 3. Sum net profit: Gross Profit + Swaps + Commissions (Handled automatically by .NetProfit)
                    totalProfit += position.NetProfit;
                    tradeCount++;
                }
            }

            // 4. Calculate mathematical expectancy safely without division-by-zero errors
            return (tradeCount > 0) ? (totalProfit / tradeCount) : 0.0;
        }

        void onBarTask1()
        {
            double profits = GetAverageTradeProfit(Positions, _indData.MagicNumber);
            
            SIG signal = _strategy.Strategy_2(_tSig, profits);
            // SIG signal = _strategy.Strategy_2(_tSig, profits);
            // SIG signal = _strategy.Strategy_3(_tSig, profits);
            
            Print($"Generated signal: {signal}");

            SIG tradePosition = SIG.NOSIG;
            int barsHeld = 0;

            // bool hasConsensus = (((_indData.HyperbolicAction == 1) || (_indData.CobbDouglasAction == 1)) && _indData.MarketAction == 1);
            // bool hasCollapse = (_indData.HyperbolicAction == -1 && _indData.CobbDouglasAction == -1 && _indData.MarketAction == -1);

            double absF = Math.Abs(_indData.FMSR_Norm);
            //bool isSqueeze = (absF <= 0.15);
            bool isSqueeze = (absF <= 0.4);

           


            // Define your volume (Example: 10,000 units = 0.10 lots)
            double volumeUnits = Symbol.QuantityToVolumeInUnits(0.1);
            // string label = "PhyBot_Signal";
            //int activeTrades = GetActivePositionsCount(label);
            int allPositions = Positions.Count;

            // Count OcNLY positions opened by this bot (using your "PhyLabel")
            var botPositions = Positions.FindAll(_label, SymbolName);
            int activeTradesCount = botPositions.Length;
            if (activeTradesCount >= 15) return;

            if (_indData.CurrSpread > _indData.SpreadLimit) // E.g., Max 3 pips spread
            {
                Print(">>> Veto: Spread too high.", " Curr spread:", _indData.CurrSpread, " Spread limit:", _indData.SpreadLimit);
                return;
            }

            //################## CLOSE LOGIC ##################
            if (activeTradesCount > 0)
            {
                foreach (var pos in botPositions)
                {
                    // _indData = _indData with { TradePosition = (pos.TradeType == TradeType.Buy) ? SIG.BUY : SIG.SELL, BarsHeld = GetBarAge(pos) };
                    // _engine.SetIndData(_indData);
                    tradePosition = (pos.TradeType == TradeType.Buy) ? SIG.BUY : SIG.SELL;
                    barsHeld = GetBarAge(pos);

                    // printData(_indData);

                    // Check if we need to close the position based on the new signal
                    if (pos.TradeType == TradeType.Buy && signal == SIG.SELL)
                    {
                        Print(">>> Closing BUY position due to SELL signal...");
                        ClosePosition(pos);
                    }
                    else if (pos.TradeType == TradeType.Sell && signal == SIG.BUY)
                    {
                        Print(">>> Closing SELL position due to BUY signal...");
                        ClosePosition(pos);
                    }
                    else if ((signal == SIG.CLOSE) && (barsHeld > 5))
                    {
                        Print(">>> Closing position due to CLOSE signal and barsheld for more than 5...");
                        ClosePosition(pos);
                    }

                }

            }
            //###########################################################

            //################## OPEN LOGIC #############################

            // if (activeTradesCount == 0)
            // {
            switch (signal)
            {
                case SIG.BUY:
                    Print(">>> BUY signal generated!");
                    // ExecuteMarketOrder(TradeType, SymbolName, Volume, Label, StopLoss, TakeProfit)
                    // ExecuteMarketOrder(TradeType.Buy, SymbolName, volumeUnits, label, 10, 20);
                    ExecuteMarketOrder(TradeType.Buy, SymbolName, volumeUnits, _label, null, null);
                    _canTradeThisBar = false;
                    break;

                case SIG.SELL:
                    Print(">>> SELL signal generated!");
                    // ExecuteMarketOrder(TradeType.Sell, SymbolName, volumeUnits, label, 10, 20);
                    ExecuteMarketOrder(TradeType.Sell, SymbolName, volumeUnits, _label, null, null);
                    _canTradeThisBar = false;
                    break;

                case SIG.HOLD:
                    // Do nothing, or monitor existing positions
                    break;
            }
            //           }

        }
    }
}