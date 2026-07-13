using System;
using System.Security.AccessControl;
namespace Phy.Lib;


public enum SIG
{
    HOLD = 101,   // 0
    BUY = 102,    // 1
    SELL = 103,   // 2
    CLOSE = 104, // 3
    TRADE = 105,// 4
    NOTRADE = 106,
    SIDEWAYS = 107, // 4
    NOSIG = 108 // 5

}


public enum DECAY_STRATEGY
{
    STRAT_ATR,       // Volatility fuel (your current)
    STRAT_ADX,       // Trend quality
    STRAT_ER,        // Efficiency
    STRAT_MIX        // Weighted mix (0.5 ATR + 0.3 ADX + 0.2 ER)
}




public struct DTYPE
{
    public double val1;
    public double val2;
    public double val3;
    public double val4;
    public double val5;
}


public readonly record struct IndData
{
    // --- 1. HISTORICAL ARRAYS (The "Primary" 500 & "Secondary" 120) ---
    // Note: In C#, arrays inside a struct are Reference Types pointing to the Heap.
    public double[] Open { get; init; }
    public double[] High { get; init; }
    public double[] Low { get; init; }
    public double[] Close { get; init; }
    public DateTime[] Time { get; init; }
    public double[] TickVolume { get; init; }
    // Indicators
    public double[] StdClose { get; init; }
    public double[] StdOpen { get; init; }
    public double[] Mfi { get; init; }
    public double[] Obv { get; init; }
    public double[] Rsi { get; init; }
    public double[] Atr { get; init; }
    public double[] Adx { get; init; }
    public double[] AdxPlus { get; init; }
    public double[] AdxMinus { get; init; }
    public double[] Ima5 { get; init; }
    public double[] Ima14 { get; init; }
    public double[] Ima30 { get; init; }
    public double[] Ima60 { get; init; }
    public double[] Ima120 { get; init; }
    public double[] Ima240 { get; init; }
    public double[] Ima500 { get; init; }
    public double[] AvgStd { get; init; }

    // --- 2. SCALARS (Trading State) ---
    public ulong MagicNumber { get; init; }
    public double CloseProfit { get; init; }
    public double StopLoss { get; init; }
    public double CurrProfit { get; init; }
    public double MaxProfit { get; init; }
    public SIG TradePosition { get; init; }
    public SIG AvgTradePosition { get; init; }
    public int CurrSpread { get; init; }
    public int Shift { get; init; }
    public int BarsHeld { get; init; }
    public double BaseSlope { get; init; }

    public double FMSR_Raw { get; init; }
    public double FMSR_Norm { get; init; }
    // --- 3. PHYSICS SCORES ---
    public double HoldScore { get; init; }
    public double BayesianHoldScore { get; init; }
    public double NeuronHoldScore { get; init; }
    public double FractalAlignment { get; init; }
    public double MicroLots { get; init; }
    public double ConvictionFactor { get; init; }
    public int PhysicsAction { get; init; }
    public int CobbDouglasAction { get; init; }
    public int HyperbolicAction { get; init; }
    public int MarketAction { get; init; }
    public double PipValue { get; init; }
    public double Point { get; init; }
    public long _Period { get; init; }
    public double PipSize { get; init; }

    public double Current_Period { get; init; }
    public double DBL_EPSILON { get; init; }
    public double SpreadLimit { get; init; }

    public bool CandleTraded { get; init; }
    public int Digits { get; init; }

    public int TotalOrders { get; init; }
}

public struct FEATURE_VECTOR
{

    // Group A: Momentum & Velocity (The Drivers)
    public double slopeIma5 { get; set; }
    public double slopeIma30 { get; set; }
    public double adxPlusMinusDiff { get; set; }
    public double rsi { get; set; }

    // Group B: Volatility & Energy (The Fuel)
    public double atr { get; set; }
    public double stdDevCP { get; set; }
    public double adx { get; set; }
    public double tVol { get; set; }

    // Group C: Structure & Stretch (The Geometry)

    public double priceElasticity { get; set; }
    public double mfi { get; set; }
    public double vWCM { get; set; }
    public double expansionCompression { get; set; }

    // Group D: The "Super-Feature" Additions.
    public double bayesianScore { get; set; }
    public double neuronScore { get; set; }
    public double fMSR_Norm { get; set; }
    public double fractalAlignment { get; set; }

}

public struct STRATEGY_STATE
{
    public bool newCandle { get; set; }
    public bool inTrade { get; set; }
}

public struct T_SIG
{
    public SIG volMomentumSIG { get; set; }
    public SIG tradeSlopeSIG { get; set; }
    public SIG slope30SIG { get; set; }
    public SIG candleVolSIG { get; set; }
    public SIG physicsSIG { get; set; }
    public SIG singleCandleVolSIG { get; set; }
    public SIG layeredMomentumSIG { get; set; }
    public SIG macroWaveSIG { get; set; }
    public SIG microWaveSIG { get; set; }
    public SIG slopeAnalyzerSIG { get; set; }
    public SIG waveTideSIG { get; set; }
    public SIG openSIG { get; set; }
    public SIG closeSIG { get; set; }
    public SIG baseSlopeSIG { get; set; }
    public SIG fastSIG { get; set; }
    public SIG fsig5 { get; set; }
    public SIG fsig14 { get; set; }
    public SIG fsig30 { get; set; }
    public SIG fsig60 { get; set; }
    public SIG fsig120 { get; set; }
    public SIG fsig240 { get; set; }
    public SIG fsig500 { get; set; }

    public SIG cpScatterSIG { get; set; }
    public SIG slopeCandle120SIG { get; set; }

    public SIG fuseFastSIG { get; set; }
    public SIG fuseSlowSIG { get; set; }

}




public class CAppState
{
    private IndData _indData;
    private readonly IUtils _utils;
    public CAppState(IndData indData, IUtils utils)
    {
        _indData = indData;
        _utils = utils;
    }

    public void SetIndData(IndData data)
    {
        this._indData = data;
    }

    // These will be saved automatically
    public double PeakSingleSlopePositive { get; set; } = 0;
    public double PeakSingleSlopeNegative { get; set; } = 0;
    public double PeakDoubleSlopePositive { get; set; } = 0;
    public double PeakDoubleSlopeNegative { get; set; } = 0;
    public double PeakProfitPositive { get; set; } = 0;
    public double PeakProfitNegative { get; set; } = 0;
    public int CurrentSignal { get; set; }
    public DateTime LastTradeTime { get; set; }
}


public class CircularBuffer<T>
{
    private readonly T[] _data;
    private int _head;
    private int _count;

    // C# Properties replace your count(), capacity(), isFull() methods
    public int Capacity { get; }
    public int Count => _count;
    public bool IsFull => _count == Capacity;
    public bool IsEmpty => _count == 0;

    public CircularBuffer(int capacity)
    {
        if (capacity <= 0) throw new ArgumentException("Capacity must be > 0");
        Capacity = capacity;
        _data = new T[capacity];
        _head = 0;
        _count = 0;
    }


    public void Push(T value)
    {
        _data[_head] = value;
        _head = (_head + 1) % Capacity;
        if (_count < Capacity) _count++;
    }

    public T Get(int logicalIndex)
    {
        if (_count == 0 || logicalIndex < 0 || logicalIndex >= _count)
        {
            return default; // Equivalent to (T)NULL in MQL4
        }
        int raw = ((_head - 1 - logicalIndex) + Capacity) % Capacity;
        return _data[raw];
    }

    public T Newest => Get(0);
    public T Oldest => Get(_count - 1);

    public void Clear()
    {
        _head = 0;
        _count = 0;
        Array.Clear(_data, 0, _data.Length); // C# native way to wipe arrays
    }

    // Updates an existing value without advancing the buffer
    public void Update(int logicalIndex, T value)
    {
        if (_count == 0 && logicalIndex == 0)
        {
            Push(value);
            return;
        }

        if (logicalIndex < 0 || logicalIndex >= _count) return;

        int raw = ((_head - 1 - logicalIndex) + Capacity) % Capacity;
        _data[raw] = value;
    }

    public void UpdateNewest(T value) => Update(0, value);
}

public class SignalHistory : CircularBuffer<SIG>
{
    public SignalHistory(int capacity) : base(capacity) { }

    // MQL4 & reference becomes C# 'out' keyword
    public void Analyse(out int valueOut, out double weightOut, out double accuracyOut)
    {
        int buys = 0, sells = 0, notrades = 0, flips = 0;
        int n = Count;

        for (int i = 0; i < n; i++)
        {
            SIG s = Get(i);
            if (s == SIG.BUY) buys++;
            if (s == SIG.SELL) sells++;
            if (s != SIG.BUY && s != SIG.SELL) notrades++;

            // flip = signal changed from previous (i+1 is one older)
            if (i < n - 1)
            {
                SIG prev = Get(i + 1);
                if (s != prev && s != SIG.NOSIG && prev != SIG.NOSIG)
                    flips++;
            }
        }

        int total = buys + sells;
        if (total == 0 || buys == sells)
        {
            valueOut = 0;
            weightOut = 0.1;
            accuracyOut = 0.5;
            return;
        }

        // MathMax becomes Math.Max in C#
        int dominant = Math.Max(buys, sells);
        double biasRatio = (double)dominant / total;
        double consistency = 1.0 - (double)flips / Math.Max(total - 1, 1);
        int direction = (sells > buys) ? -1 : 1;

        int magnitude = (biasRatio >= 0.75) ? 3 :
                        (biasRatio >= 0.60) ? 2 : 1;

        valueOut = direction * magnitude;
        weightOut = 0.5 + (consistency * 2.5);
        accuracyOut = 0.5 + (biasRatio - 0.5) * 0.5;
    }

    // MQL4 pointers become standard C# references
    public double CalculateAgreement(SignalHistory otherBuffer)
    {
        // C# handles null checks cleanly
        if (otherBuffer == null || this.Count != otherBuffer.Count || this.Count == 0)
            return 0.5;

        int n = Count;
        int matches = 0;
        int validComparisons = 0;

        for (int i = 0; i < n; i++)
        {
            SIG sigA = this.Get(i);
            SIG sigB = otherBuffer.Get(i);

            if (sigA != SIG.NOSIG && sigB != SIG.NOSIG)
            {
                validComparisons++;
                if (sigA == sigB) matches++;
            }
        }

        if (validComparisons == 0) return 0.5;

        double rawAgreement = (double)matches / validComparisons;
        return Math.Abs(rawAgreement - 0.5) * 2.0;
    }
}