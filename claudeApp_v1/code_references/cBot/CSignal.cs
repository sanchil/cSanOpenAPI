using System;
using System.Linq.Expressions;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
namespace Phy.Lib;

public interface ISignal
{
    public SIG GetPhysicsSignal();
    public SIG VolatilityMomentumSIG();
    public SIG GetSignal();

    public SIG TradeSlopeSIG(in DTYPE fast, in DTYPE slow, int magicnumber = -1);

    public SIG MicroWaveSIG(in DTYPE fast, in DTYPE med);
    public SIG MacroWaveSIG(in DTYPE fast, in DTYPE slow);
    public SIG SlopeAnalyzerSIG(in DTYPE slope);
    public SIG LayeredMomentumSIG(in double[] signal, int N = 20);

    public SIG CandleVolSIG(in double[] open, in double[] close, in double[] volume, in double atr, int period = 30, int SHIFT = 1);
    public SIG WaveTideSIG(in DTYPE fast, in DTYPE med, in DTYPE slow);
    public T_SIG InitSignal();
    public void InitHistory(in T_SIG tSIG, bool newBar);
    public T_SIG InitSecSignal(bool newBar);

    public T_SIG GetT_SIG();
    public SIG GetCloseSignal();
    public SIG fuseFastSIG(in T_SIG tSIG, in bool newBar);
    public SIG fuseSlowSIG(in T_SIG tSIG, in bool newBar);


    public SIG fastSlowSIG(
       in double fastSig,
       in double slowSig,
       double thresholdPct = 0.0005 // e.g., 0.0005 for 0.05% separation
    );

    //+------------------------------------------------------------------+
    //| Kinetic Acceleration Engine (Unitless & Stationary)              |
    //| Computes acceleration of a single signal line over two periods.  |
    //+------------------------------------------------------------------+
    public SIG kineticAccelerationSIG(
       in double fastSlope,      // e.g., 3-period slope
       in double slowSlope,      // e.g., 10-period slope
       double tradeZoneCheck = 0.02, // do not trade if slow slope is flat-lining
       double tradeCloseLimit = -0.08,   // The ratio limit which closes this trade call
       string funcLabel = ""   // Which function makes this call
    );

    //+------------------------------------------------------------------+
    //| fuseSIG — Final Weighted Fusion for Fast Signals in MQL4        |
    //|                                                                  |
    //| • Weights: Prioritize stronger signals (e.g., RSI=2.0, MA=1.0)  |
    //| • HOLD: For weak agreements — reduces whipsaws in forex ranges   |
    //| • Chains recursively: FuseSIG(FuseSIG(A,B),C)                   |
    //| • MQL4-safe: Use in OnCalculate() or OnTick()                   |
    //+------------------------------------------------------------------+
    public SIG fuseSIG(SIG a, SIG b, double weightA = 1.0, double weightB = 1.0);

}


public class CSignal : ISignal
{
    private PhysicsEngine _engine;

    private SIG _tacticalSignal;
    private CStats _stats;
    private CUtils _utils;
    private T_SIG _tSig;

    private static readonly double[] closeRVal = { 1.3, 1.2, 1.1, 1.0, 0.9 };

    private double m_peakRatio;  // class member
    private DateTime m_last_bar;
    private SIG m_cached;


    SignalHistory slope30Hist,
                  baseSlopeHist,
                  fastHist,
                  micWaveHist,
                  macWaveHist,
                  waveTideHist,
                  slopeCandle120Hist,
                  cpScatterHist,
                  candleVolHist,
                  volMomHist,
                  slopeAnalyzerHist,
                  tradeSlopeHist,
                  momHist;


    public CSignal(PhysicsEngine engine, CStats stats, CUtils utils)
    {
        _engine = engine;
        _stats = stats;
        _utils = utils;
        _tacticalSignal = SIG.HOLD;
        // CRITICAL: Instantiate all history buffers here!
        // Replace '12' with your required minimum history period.

        // slope30Hist = new SignalHistory(12);
        // baseSlopeHist = new SignalHistory(12);
        // fastHist = new SignalHistory(12);
        // micWaveHist = new SignalHistory(12);
        // macWaveHist = new SignalHistory(12);
        // waveTideHist = new SignalHistory(12);
        // slopeCandle120Hist = new SignalHistory(12);
        // cpScatterHist = new SignalHistory(12);
        // candleVolHist = new SignalHistory(12);
        // volMomHist = new SignalHistory(12);
        // slopeAnalyzerHist = new SignalHistory(12);
        // tradeSlopeHist = new SignalHistory(12);
        // momHist = new SignalHistory(12);


        _tSig = InitSignal();
    }

    public T_SIG GetT_SIG()
    {
        return _tSig;
    }

    public SIG VolatilityMomentumSIG()
    {
        double strictness = 1.0; // Adjust this to make the strategy more or less aggressive
                                 // 1. KINETIC GATE (Is the market physically moving?)
                                 // Use your Universal Physics Engine. No more raw pip calculations.

        int SHIFT = _engine.GetIndData().Shift;

        double kineticEnergy = _engine.atrKinetic();

        // If Kinetic Energy is extremely low, the market is completely dead.
        if (kineticEnergy < (0.10 * strictness))
            return SIG.NOTRADE;

        // 2. POTENTIAL GATE (Is there a trend context?)
        // "Is the atmosphere charged?"
        double potential = _engine.adxPotential();

        // Gate: If potential is too low (< 0.75 = ADX 15), the market is wandering.
        if (potential < (0.75 * strictness))
            return SIG.NOTRADE;


        double normalizedScore = _engine.VolatilityEfficiency();

        // 4. DECISION: Dynamic Physics Threshold
        double baseThreshold = 0.40;

        // Discount Logic: Strong trends reduce the work required.
        // Lowered multiplier from 0.50 to 0.20 so we don't discount too aggressively.
        double discount = 0.20 * (potential - 1.0);
        double finalThreshold = (baseThreshold - discount) * strictness;

        // CRITICAL SAFETY FIX:
        // We MUST require at least *some* true volatility expansion (e.g., 0.15).
        // If we let this drop to 0.0 or negative, MathAbs() will allow false signals.
        finalThreshold = Math.Max(finalThreshold, 0.15);

        double slope30 = _stats.slopesVal(_engine.GetIndData().Ima30, shift: SHIFT).val2;

        // 5. TRIGGER
        if (Math.Abs(normalizedScore) > finalThreshold)
        {
            if (slope30 > 0.1) return SIG.BUY;
            else if (slope30 < -0.1) return SIG.SELL;
        }
        return SIG.NOTRADE;
    }
    public T_SIG InitSignal()
    {
        T_SIG tSig = new T_SIG();
        IndData indData = _engine.GetIndData();
        tSig.baseSlopeSIG = kineticAccelerationSIG(_stats.slopesVal(indData.Ima240).val1, _stats.slopesVal(indData.Ima240).val2, 0.015, -0.06, "BASE_SLOPE");
        tSig.slope30SIG = kineticAccelerationSIG(_stats.slopesVal(indData.Ima30).val1, _stats.slopesVal(indData.Ima30).val2, 0.015, -0.2, "SLOPE_30");
        tSig.fsig5 = fastSlowSIG(indData.Close[1], indData.Ima5[1], 0.0005);
        tSig.fsig30 = fastSlowSIG(indData.Close[1], indData.Ima30[1], 0.0005);
        tSig.fsig60 = fastSlowSIG(indData.Close[1], indData.Ima60[1], 0.0005);
        tSig.fsig240 = fastSlowSIG(indData.Close[1], indData.Ima240[1], 0.0005);
        tSig.fastSIG = (fuseSIG(fuseSIG(tSig.fsig5, tSig.fsig14), tSig.fsig30) != SIG.NOSIG) ? fuseSIG(fuseSIG(tSig.fsig5, tSig.fsig14), tSig.fsig30) : SIG.CLOSE;

        tSig.volMomentumSIG = VolatilityMomentumSIG();
        tSig.tradeSlopeSIG = TradeSlopeSIG(_stats.slopesVal(indData.Ima30), _stats.slopesVal(indData.Ima60));
        tSig.slope30SIG = SlopeAnalyzerSIG(_stats.slopesVal(indData.Ima30));
        tSig.candleVolSIG = CandleVolSIG(indData.Open, indData.Close, indData.TickVolume, indData.Atr[indData.Shift]);
        tSig.singleCandleVolSIG = new SingleCandleVolSIG(_engine).Analyze(indData.Open, indData.Close, indData.TickVolume, indData.Atr[indData.Shift]);
        tSig.layeredMomentumSIG = LayeredMomentumSIG(indData.Ima30);

        tSig.microWaveSIG = MicroWaveSIG(_stats.slopesVal(indData.Ima30), _stats.slopesVal(indData.Ima60));
        tSig.macroWaveSIG = MacroWaveSIG(_stats.slopesVal(indData.Ima30), _stats.slopesVal(indData.Ima60));
        tSig.waveTideSIG = WaveTideSIG(_stats.slopesVal(indData.Ima30), _stats.slopesVal(indData.Ima60), _stats.slopesVal(indData.Ima120));
        tSig.physicsSIG = GetPhysicsSignal();

        return tSig;
    }




    public void InitHistory(in T_SIG tSIG, bool newBar)
    {
        if (newBar)
        {
            slope30Hist.Push(tSIG.slope30SIG);
            baseSlopeHist.Push(tSIG.baseSlopeSIG);
            fastHist.Push(tSIG.fastSIG);
            micWaveHist.Push(tSIG.microWaveSIG);
            macWaveHist.Push(tSIG.macroWaveSIG);
            waveTideHist.Push(tSIG.waveTideSIG);
            slopeCandle120Hist.Push(tSIG.slopeCandle120SIG);
            cpScatterHist.Push(tSIG.cpScatterSIG);
            candleVolHist.Push(tSIG.candleVolSIG);
            volMomHist.Push(tSIG.volMomentumSIG);
            slopeAnalyzerHist.Push(tSIG.slopeAnalyzerSIG);
            tradeSlopeHist.Push(tSIG.tradeSlopeSIG);
            momHist.Push(tSIG.layeredMomentumSIG);

        }
    }

    public T_SIG InitSecSignal(bool newBar)
    {
        T_SIG tSig = InitSignal();

        InitHistory(tSig, newBar);

        if (volMomHist != null && volMomHist.IsFull)
        {
            tSig.fuseFastSIG = fuseFastSIG(tSig, true);
            tSig.fuseSlowSIG = fuseSlowSIG(tSig, true);
        }
        else
        {
            // Default state for the "Warm-up" period
            tSig.fuseFastSIG = SIG.NOSIG;
            tSig.fuseSlowSIG = SIG.NOSIG;
        }

        return tSig;
    }

    //    public SIG GetSignal()
    //     {
    //         SIG signal = VolatilityMomentumSIG();
    //         _tacticalSignal = signal;
    //         return signal;
    //     }

    public SIG GetSignal()
    {
        // _tSig = InitSignal();
        SIG OpenSig = _tSig.tradeSlopeSIG;
        SIG CloseSig = _tSig.microWaveSIG;
        _tSig.openSIG = OpenSig;
        _tSig.closeSIG = CloseSig;
        _tacticalSignal = OpenSig;
        return OpenSig;
    }

    public SIG GetCloseSignal()
    {
        // if (_tSig.closeSIG == SIG.NOTRADE)
        // {
        //     return _tSig.microWaveSIG; // Fallback to volatility momentum if no specific close signal
        // }
        if (_utils.OppSignal(_tSig.openSIG, _tSig.microWaveSIG))
        {
            return SIG.CLOSE;
        }
        return SIG.NOSIG;
    }


    public SIG GetPhysicsSignal()
    {
        double velocity = _engine.GetVelocity(_engine.GetIndData().Ima30);
        double acceleration = _engine.GetAcceleration(_engine.GetIndData().Ima30);
        double zScore = _engine.GetMomentumZScore(_engine.GetIndData().Ima30);

        // If velocity is high, acceleration is positive, and it's not a 'freak' move (Z < 2.0)
        if (velocity > 0.5 && acceleration > 0 && zScore < 2.0)
        {
            return SIG.BUY;
        }

        // Mean Reversion: High velocity but negative acceleration at a high Z-score
        if (zScore > 2.5 && acceleration < 0)
        {
            return SIG.SELL;
        }

        return SIG.HOLD;
    }


    public SIG TradeSlopeSIG(in DTYPE fast, in DTYPE slow, int magicnumber = -1)
    {

        // --- 1. BAR OPENING CHECK (Fixed State Management) ---
        // Uses class members m_last_bar and m_cached instead of static variables
        // Passed Time[0] as a parameter or fetched cleanly.
        IndData indData = _engine.GetIndData();
        if (indData.Time[0] == m_last_bar)
            return m_cached;



        // --- THE GHOST PEAK FIX ---
        // If the EA currently holds no positions, we MUST reset the peak.
        double totalOrders = indData.TotalOrders;
        if (totalOrders == 0 && m_peakRatio > 0)
        {
            m_peakRatio = 0;
            m_cached = SIG.NOSIG;
        }
        // END OF GHOST PEAK FIX

        m_last_bar = indData.Time[0];
        double atr = indData.Atr[indData.Shift];

        // --- 2. DYNAMIC CONSTANTS & INPUTS ---
        const double MIN_SLOW_THRESHOLD = 0.0001; // Avoid division by zero

        // The Elastic Ruler: We define slope thresholds as fractions of the ATR.
        // Example: If ATR is 10 pips, FLAT is anything moving less than 0.5 pips/bar.
        double FLAT_REGIME_ENTRY = atr * 0.05;  // 5% of ATR
        double WEAK_TREND_BOUND = atr * 0.10;  // 10% of ATR
        double MID_TREND_BOUND = atr * 0.20;  // 20% of ATR
        double STRONG_TREND_BOUND = atr * 0.35;  // 35% of ATR
        double TURBO_TREND_BOUND = atr * 0.50;  // 50% of ATR

        double fastSlope = fast.val1;
        double slowSlope = slow.val1;
        double absSlow = Math.Abs(slowSlope);

        // --- 3. ADAPTIVE REGIME SELECTION ---
        // We replace your static 0.35, 0.80, 1.50 guesses with the ATR boundaries.
        int regimeIdx = (absSlow <= WEAK_TREND_BOUND) ? 0 :
                        (absSlow <= MID_TREND_BOUND) ? 1 :
                        (absSlow <= STRONG_TREND_BOUND) ? 2 :
                        (absSlow <= TURBO_TREND_BOUND) ? 3 : 4;

        // .NET 8 optimized: The compiler handles the safety and performance automatically
        // ReadOnlySpan<double> closeRVal = [1.3, 1.2, 1.1, 1.0, 0.9];
        // This lives in memory once and never moves. Very fast.
        ReadOnlySpan<double> closeRatios = closeRVal;
        double CLOSERATIO = closeRatios[regimeIdx];

        double PEAK_DROP = _engine.getVolAdaptiveRetention();
        PEAK_DROP = Math.Max(Math.Min(PEAK_DROP, 0.99), 0.70); // Hard clamp

        // --- 4. CORE LOGIC BRANCHING ---

        // BRANCH A: The "Flat" Market (Singularity Avoidance)
        if (absSlow < MIN_SLOW_THRESHOLD)
        {
            // Reversal Check
            if ((m_cached == SIG.BUY && fastSlope < -FLAT_REGIME_ENTRY) ||
                  (m_cached == SIG.SELL && fastSlope > FLAT_REGIME_ENTRY))
            {
                m_peakRatio = 0;
                m_cached = SIG.CLOSE;
                return SIG.CLOSE;
            }

            // Entry Logic
            if (Math.Abs(fastSlope) > FLAT_REGIME_ENTRY)
            {
                if (m_peakRatio == 0) m_peakRatio = CLOSERATIO * 1.05;
                m_cached = (fastSlope > 0) ? SIG.BUY : SIG.SELL;
                return m_cached;
            }

            // FLAT MARKET ORPHAN FIX: If momentum died while holding a trade, close it.
            if (m_peakRatio > 0)
            {
                m_peakRatio = 0;
                m_cached = SIG.CLOSE;
                return SIG.CLOSE;
            }

            m_peakRatio = 0;
            m_cached = SIG.NOSIG;
            return SIG.NOSIG;
        }

        // BRANCH B: The Standard Adaptive Engine
        double ratio = fastSlope / slowSlope;

        // // Use PrintFormat for efficiency
        // PrintFormat("Ratio=%.3f | Peak=%.3f | DropLimit=%.3f | Regime=%d",
        //             ratio, m_peakRatio, (PEAK_DROP * m_peakRatio), regimeIdx);

        // 1. INSTANT REVERSAL (Divergence Check)
        if (ratio <= 0)
        {
            m_peakRatio = 0;
            m_cached = SIG.CLOSE;
            return SIG.CLOSE;
        }

        // 2. MOMENTUM DECAY EXIT (The Adaptive Stop)
        if (m_peakRatio > 0 && ratio < (PEAK_DROP * m_peakRatio))
        {
            m_peakRatio = 0;
            m_cached = SIG.CLOSE;
            return SIG.CLOSE;
        }

        // 3. WEAK ALIGNMENT EXIT (Hard Floor)
        if (ratio <= CLOSERATIO)
        {
            m_peakRatio = 0;
            m_cached = SIG.CLOSE;
            return SIG.CLOSE;
        }

        // 4. ENTRY & CONTINUATION (New Peak Tracking)
        if (ratio > CLOSERATIO)
        {
            if (ratio > m_peakRatio) m_peakRatio = ratio;
            m_cached = (fastSlope > 0) ? SIG.BUY : SIG.SELL;
            return m_cached;
        }

        m_cached = SIG.NOSIG;
        return SIG.NOSIG;
    }


    public SIG MicroWaveSIG(in DTYPE fast, in DTYPE med)
    {

        // 1. THE MICRO-FLOOR (Aggressive)
        // We lower the barrier to entry. We only need 10% of ATR to consider it "Active."
        IndData indData = _engine.GetIndData();
        double atr = indData.Atr[indData.Shift];

        double MICRO_FLOOR = atr * 0.10;
        double NOTRADEZONE = MICRO_FLOOR * 1.5;


        double fS = fast.val1;
        double mS = med.val1;
        double absSlow = Math.Abs(mS);

        SIG dir = (fS > 0) ? SIG.BUY : SIG.SELL;

        // 2. THE VELOCITY CHECK (The "Explosion" Gate)
        // We use slopeRatio but we pass our lower MICRO_FLOOR.
        // We want to see the Wave pulling away from the Current.
        double vScore = _engine.expansionCompressionRatio(fS, mS, MICRO_FLOOR);

        // 3. THE MICRO-POLICY
        // We ONLY enter if the expansion is nearly perfect (>= 0.95)
        // This ensures we are catching the "Meat" of the micro-move.
        //        if (vScore >= 0.95)
        //       if (vScore >= 0.7)
        if (vScore >= 0.7)
        {
            return dir;
        }

        // 4. THE LIGHTNING EXIT
        // If the velocity score drops even slightly (e.g., below 0.70),
        // we BAIL. There is no macro structure to save us here.
        if ((vScore < 0.70) && (absSlow <= NOTRADEZONE)) return SIG.CLOSE;
        if ((vScore < 0.70) && (absSlow > NOTRADEZONE))
        {
            return SIG.NOSIG;
        }

        return SIG.NOSIG;
    }


    public SIG MacroWaveSIG(in DTYPE fast, in DTYPE slow)
    {

        IndData indData = _engine.GetIndData();
        double atr = indData.Atr[indData.Shift];
        double floor = atr * 0.30;
        double NOTRADEZONE = (floor * 1.5);


        double fS = fast.val1;
        double sS = slow.val1;
        double absSlow = Math.Abs(sS);


        SIG dir = (sS > 0) ? SIG.BUY : SIG.SELL;
        //const double SLOPERANGELIMIT = 0.3;

        // 1. CALL THE PHYSICS ENGINE
        // This one call handles Directional Alignment AND the Structural Floor.
        //double mScore = ms.slopeRatio(fS, sS, SLOPERANGELIMIT);
        // To this:
        double mScore = _engine.expansionCompressionRatio(fS, sS, floor);


        // 2. THE LEAN POLICY
        // If mScore is 1.0, the Metric has already verified Direction, Floor, and Expansion.
        if (mScore >= 0.9) return dir;

        // For Case B (Compression), we check for the "Power Trend" extra requirement.
        if (mScore >= 0.8 && absSlow >= NOTRADEZONE) return dir;

        // 3. FLATSQUEEZE: Squeezing/Weak and the base trend is flat
        if (mScore < 0.8 && absSlow < NOTRADEZONE) return SIG.CLOSE;

        // 4. BUYSQUEEZE / SELLSQUEEZE: Squeezing but the trend is heavily sloped
        if (mScore < 0.8 && absSlow >= NOTRADEZONE)
        {
            //if(dir == SIG.BUY) {
            //   return SIG.SELL;
            //}
            //if(dir == SIG.SELL) {
            //   return SIG.BUY;
            //}
            return SIG.NOSIG;
        }


        if (mScore == 0) return SIG.CLOSE;


        // If the Metric returned 0.0 (Veto) or a weak ratio, we bail.
        return SIG.CLOSE;
    }
    //+------------------------------------------------------------------+
    //|                                                                  |
    //+------------------------------------------------------------------+
    public SIG WaveTideSIG(in DTYPE fast, in DTYPE med, in DTYPE slow)
    {

        // THE TRIPLE-GEOMETRY CHAIN
        SIG waveSignal = MacroWaveSIG(fast, med);  // Micro-Expansion
        SIG tideSignal = MacroWaveSIG(med, slow);  // Macro-Expansion

        if (waveSignal == SIG.BUY && tideSignal == SIG.BUY) return SIG.BUY;
        if (waveSignal == SIG.SELL && tideSignal == SIG.SELL) return SIG.SELL;
        return SIG.NOSIG;
    }

    //+------------------------------------------------------------------+
    //|                                                                  |
    //+------------------------------------------------------------------+
    public SIG SlopeAnalyzerSIG(in DTYPE slope)
    {
        SlopeAnalyzer analyzer = new SlopeAnalyzer(_engine);
        return analyzer.Analyze(slope.val1);
    }

    //+------------------------------------------------------------------+
    //| Layered Filter: ADX → Histogram for Momentum Strength            |
    //+------------------------------------------------------------------+
    public SIG LayeredMomentumSIG(in double[] signal, int N = 20)
    {

        double gate = _engine.layeredMomentumFilter(signal, N);
        if (gate == 0)
            return SIG.NOSIG;
        if (gate == 1)
            return SIG.BUY;
        if (gate == -1)
            return SIG.SELL;
        return SIG.NOSIG;
    }


    //+------------------------------------------------------------------+
    //|                                                                  |
    //+------------------------------------------------------------------+
    public SIG CandleVolSIG(
            in double[] open,
            in double[] close,
            in double[] volume,
            in double atr,
            int period = 30,
            int SHIFT = 1
            )
    {

        IndData indData = _engine.GetIndData();
        double atr_pips = atr / indData.Point;

        double atr_floor = atr_pips * 0.05;

        // 1. DYNAMIC WINDOWS (Guaranteed to be distinct)
        //int fast_n = (int)(period * 0.5);
        int fast_n = Math.Max(5, (int)(period * 0.4));  // 40% instead of 50% — faster response

        if (fast_n < 5) fast_n = 5; // Minimum 5 candles to avoid noise

        // 2. RAW KINEMATICS
        double slow = _engine.vWCM_Raw(10);
        double fast = _engine.vWCM_Raw(10);

        if (Math.Abs(slow) < atr_floor) return SIG.NOSIG;  // veto very flat volume


        // 3. DIRECTIONAL AGREEMENT
        bool agree_dir = (slow > 0 && fast > 0) || (slow < 0 && fast < 0);

        // 4. MOMENTUM RATIO (Fast must maintain at least 75% of Slow's power)
        // We use MathAbs to safely compare the force regardless of direction
        //bool agree_str = (MathAbs(fast) / (MathAbs(slow) + DBL_EPSILON)) > 0.75;
        bool agree_str = (Math.Abs(fast) / (Math.Abs(slow) + indData.DBL_EPSILON)) > 0.6;

        // 5. THE VERDICT
        SIG sig = SIG.NOSIG;
        if (agree_dir && agree_str)
        {
            sig = (slow > 0) ? SIG.BUY : SIG.SELL;
        }

        // [Optional Debugging Log]
        // PrintFormat("vWCM | Slow:%.4f Fast:%.4f → %s", slow, fast, util.getSigString(sig));

        return sig;
    }



    public SIG fuseFastSIG(in T_SIG tSIG, in bool newBar)
    {
        SIG sig = SIG.NOSIG;
        int ANALYZECOUNT = 4; // strictly fast components
        int[] v;
        double[] w, a, ind;

        v = new int[ANALYZECOUNT];
        w = new double[ANALYZECOUNT];
        a = new double[ANALYZECOUNT];
        ind = new double[ANALYZECOUNT];

        double prob = -1;

        if (volMomHist.IsFull)
        {
            // 1. Kinetic Energy (Fast Volatility)
            volMomHist.Analyse(out v[0], out w[0], out a[0]);
            ind[0] = 1.0; // Independent Dimension (Energy)

            // 2. Current Candle Force (Volume)
            candleVolHist.Analyse(out v[1], out w[1], out a[1]);
            ind[1] = 1.0; // Independent Dimension (Volume)

            // 3. Fast Price Action / MA Crossover
            fastHist.Analyse(out v[2], out w[2], out a[2]);
            ind[2] = 0.5; // Shared Dimension (Price)

            // 4. Micro Wave Structure
            micWaveHist.Analyse(out v[3], out w[3], out a[3]);
            ind[3] = 0.5; // Shared Dimension (Price)

            // Apply the static independence discount BEFORE fusion
            for (int i = 0; i < ANALYZECOUNT; i++)
            {
                w[i] = w[i] * ind[i];
            }

            prob = _engine.fuseProbability(v, w, a, ANALYZECOUNT);

            //   Print("FAST Ensemble — count: ", volMomHist.count(), " Prob: ", NormalizeDouble(prob,4));

            if (prob >= 0.70) return SIG.BUY;  // Tightened to 0.70 for Fast noise
            if (prob <= 0.30) return SIG.SELL;
            return SIG.NOTRADE;
        }
        return sig;
    }

    public SIG fuseSlowSIG(in T_SIG tSIG, in bool newBar)
    {
        SIG sig = SIG.NOSIG;
        int ANALYZECOUNT = 7; // strictly macro/structural components

        int[] v;
        double[] w, a, ind;

        v = new int[ANALYZECOUNT];
        w = new double[ANALYZECOUNT];
        a = new double[ANALYZECOUNT];
        ind = new double[ANALYZECOUNT];

        double prob = -1;

        if (volMomHist.IsFull)
        {
            // 1. The Core Trend (Slope 30)
            slope30Hist.Analyse(out v[0], out w[0], out a[0]);
            ind[0] = 0.4; // Heavily correlated with other MAs

            // 2. The Macro Tide (Base Slope)
            baseSlopeHist.Analyse(out v[1], out w[1], out a[1]);
            ind[1] = 0.4; // Heavily correlated with other MAs

            // 3. Trade Slope / Trajectory
            tradeSlopeHist.Analyse(out v[2], out w[2], out a[2]);
            ind[2] = 0.4; // Heavily correlated with other MAs

            // 4. Macro Wave Alignment
            macWaveHist.Analyse(out v[3], out w[3], out a[3]);
            ind[3] = 0.5; // Alignment Geometry

            // 5. Wave vs Tide Harmony
            waveTideHist.Analyse(out v[4], out w[4], out a[4]);
            ind[4] = 0.5; // Alignment Geometry

            // 6. Macro Scatter/Volatility Expansion
            cpScatterHist.Analyse(out v[5], out w[5], out a[5]);
            ind[5] = 1.0; // Independent (Variance/Distribution)

            // 7. Slope Analyzer (The overarching meta-state)
            slopeAnalyzerHist.Analyse(out v[6], out w[6], out a[6]);
            ind[6] = 1.0; // Independent (Meta-logic)

            // Apply the static independence discount BEFORE fusion
            for (int i = 0; i < ANALYZECOUNT; i++)
            {
                w[i] = w[i] * ind[i];
            }

            prob = _engine.fuseProbability(v, w, a, ANALYZECOUNT); // Now properly fuses all 7!

            // Print("SLOW Ensemble — count: ", volMomHist.count(), " Prob: ", NormalizeDouble(prob, 4));

            if (prob >= 0.65) return SIG.BUY;
            if (prob <= 0.35) return SIG.SELL;
            return SIG.NOTRADE;
        }
        return sig;
    }

    //+------------------------------------------------------------------+
    //| Universal PPO Structural Signal                                  |
    //| Inputs can be Prices, MAs, or zero-centered Oscillators.         |
    //+------------------------------------------------------------------+
    //SAN_SIGNAL SanSignals::universalFastSlowSIG(
    public SIG fastSlowSIG(
       in double fastSig,
       in double slowSig,
       double thresholdPct = 0.0005 // e.g., 0.0005 for 0.05% separation
    )
    {
        // 1. Guard against division by zero (Epsilon check)
        if (Math.Abs(slowSig) < 0.000001)
        {
            // If the slow baseline is effectively zero, we look at raw delta
            if (fastSig > 0) return SIG.BUY;
            if (fastSig < 0) return SIG.SELL;
            return SIG.SIDEWAYS;
        }

        // 2. The Universal PPO Math (Absolute Denominator)
        double ppo = (fastSig - slowSig) / Math.Abs(slowSig);

        // 3. Directional Gates
        //if(ppo > thresholdPct)  return SAN_SIGNAL::BUY;
        //if(ppo < -thresholdPct) return SAN_SIGNAL::SELL;

        if (ppo > 0) return SIG.BUY;
        if (ppo < 0) return SIG.SELL;
        //  if(ppo == 0) return SAN_SIGNAL::SIDEWAYS;

        return SIG.SIDEWAYS; // Caught in the noise band
    }

    //+------------------------------------------------------------------+
    //| Kinetic Acceleration Engine (Unitless & Stationary)              |
    //| Computes acceleration of a single signal line over two periods.  |
    //+------------------------------------------------------------------+
    public SIG kineticAccelerationSIG(
       in double fastSlope,      // e.g., 3-period slope
       in double slowSlope,      // e.g., 10-period slope
       double tradeZoneCheck = 0.02, // do not trade if slow slope is flat-lining
       double tradeCloseLimit = -0.08,   // The ratio limit which closes this trade call
       string funcLabel = ""    // Which function makes this call
    )
    {
        double absSlow = Math.Abs(slowSlope);

        //const double TRADE_OPEN_LIMIT = -0.05;
        //const double TRADE_CLOSE_LIMIT = -0.08;

        const double TRADE_OPEN_LIMIT = -0.05;
        double TRADE_CLOSE_LIMIT = tradeCloseLimit;

        double ratioPrint = (fastSlope - slowSlope) / (slowSlope + 0.000001);
        //    Print("SLOPERATIO-"+funcLabel+": "+ NormalizeDouble(ratioPrint,4)+" absSlow: "+NormalizeDouble(absSlow,4)+" fast: "+NormalizeDouble(fastSlope,4));

        // 1. Zero-Divide Guard (Hard safety limit to prevent MQL4 crashes)
        if (absSlow < 0.000001)
        {
            //return SAN_SIGNAL::NOSIG;
            return SIG.CLOSE;
        }
        //Print("STEP 1");
        // 2. The Macro Flat-Line Filter (The Kinetic Floor)
        // If the macro trend lacks basic kinetic energy, the acceleration ratio is meaningless.
        if (absSlow <= tradeZoneCheck)
        {
            //return SAN_SIGNAL::NOSIG;
            return SIG.CLOSE;
        }
        //Print("STEP 2");

        // 3. The Acceleration Ratio
        double ratio = (fastSlope - slowSlope) / slowSlope;
        //  Print("SLOPERATIO: "+ ratio+" absSlow: "+absSlow+" fast: "+fastSlope);
        //Print("[BOOLCHK]: Zero Chk: "+(absSlow < 0.000001)+" TradeZone Check: "+(absSlow <= tradeZoneCheck)+" absSlow: "+absSlow+" tradeZoneCheck: "+tradeZoneCheck);

        // 4. The Execution Gates
        if (ratio >= TRADE_OPEN_LIMIT)
        {
            // Momentum is accelerating in the direction of the fast slope
            if (fastSlope > 0.0) return SIG.BUY;
            if (fastSlope < 0.0) return SIG.SELL;
        }
        //Print("STEP 3");
        //if((ratio < TRADE_OPEN_LIMIT)&&(ratio>=TRADE_CLOSE_LIMIT)) {
        //   if(slowSlope > 0.0) return SAN_SIGNAL::BUY;
        //   if(slowSlope < 0.0) return SAN_SIGNAL::SELL;
        //}

        if (ratio < TRADE_CLOSE_LIMIT)
        {
            //Momentum is heavily decelerating or reversing (Kill switch)
            return SIG.CLOSE;
            //// Return no sig on loss of momentum instead of close.
            //// This is an experiment because loss of momentum is usually temporary
            //// Close on loss of momenttum seems to be capturing only losses.
            //// Instead close only when the slope is flattening.
            // Note:
            // Must return close on less than Trade close limit
            // It is better to train and fine tune the trade close limit
            //  leaving close on flat is not a great idea
            // definitely close trades on flat. That must be prune all losses
            // however for profit booking we must plan our exits based on momentum losses

            //return SAN_SIGNAL::NOSIG;
        }
        //Print("STEP 4");


        // 5. The "No Man's Land" (-0.20 to -0.10).
        // Mild deceleration. We hold current trades but don't force a close.
        return SIG.NOSIG;
    }

    //+------------------------------------------------------------------+
    //| fuseSIG — Final Weighted Fusion for Fast Signals in MQL4        |
    //|                                                                  |
    //| • Weights: Prioritize stronger signals (e.g., RSI=2.0, MA=1.0)  |
    //| • HOLD: For weak agreements — reduces whipsaws in forex ranges   |
    //| • Chains recursively: FuseSIG(FuseSIG(A,B),C)                   |
    //| • MQL4-safe: Use in OnCalculate() or OnTick()                   |
    //+------------------------------------------------------------------+
    public SIG fuseSIG(SIG a, SIG b, double weightA = 1.0, double weightB = 1.0)
    {
        // Convert to scores: BUY= +weight, SELL= -weight, HOLD/NOSIG=0
        double scoreA = (a == SIG.BUY) ? weightA : (a == SIG.SELL) ? -weightA : 0.0;
        double scoreB = (b == SIG.BUY) ? weightB : (b == SIG.SELL) ? -weightB : 0.0;

        double total = scoreA + scoreB;
        double absTotal = Math.Abs(total);

        if (absTotal > 0.5)  // Strong consensus
            return (total > 0) ? SIG.BUY : SIG.SELL;
        if (absTotal < 0.1)  // Weak or conflicting
            return SIG.HOLD;     // Hold position or wait
        return SIG.CLOSE;       // Clear conflict
    }

}



//+------------------------------------------------------------------+
//| singleCandleVolSIG - Final, bulletproof version                     |
//+------------------------------------------------------------------+

class SingleCandleVolSIG
{
    private readonly IPhysicsEngine _engine;

    private DateTime last_bar;
    private SIG cached;

    public SingleCandleVolSIG(IPhysicsEngine eng)
    {
        _engine = eng;
        last_bar = DateTime.MinValue; ;
        cached = SIG.NOSIG;
    }
    public SIG Analyze(
       in double[] open,
       in double[] close,
       in double[] volume,
       in double atr,
       int period = 30,
       int SHIFT = 1
       )
    {
        IndData indData = _engine.GetIndData();


        if (indData.Time[0] == last_bar)
            return cached;


        last_bar = indData.Time[0];

        double atr_pips = atr / indData.Point;
        //if(atr_pips < 8.0) {
        //   cached = SAN_SIGNAL::NOSIG;
        //   return cached;
        //}
        //double slow = stats.vWCM_Score(open, close, volume, period,0,SHIFT);
        double slow = _engine.vWCM_Raw(10);
        // Print("[SLOWVCM]: " + slow);

        if ((slow > -0.05) && (slow < 0.1))
            cached = SIG.NOSIG;
        if (slow >= 0.1)
            cached = SIG.BUY;
        if (slow <= -0.05)
            cached = SIG.SELL;

        //cached = (slow > 0) ? SAN_SIGNAL::BUY : SAN_SIGNAL::SELL;

        //PrintFormat("vWCM | ATR:%.1f pips | Slow:%.4f",
        //            atr_pips, slow,
        //            cached==BUY?"BUY":cached==SELL?"SELL":"NOSIG");

        return cached;
    }

}





class SlopeAnalyzer
{
    // --- 1. State Memory (Moved from Function-Static to Class-Fields) ---
    // These replace 'static double peakPositive = 0;'
    private double _peakPositive = 0;
    private double _peakNegative = 0;
    private SIG _currentIdx = SIG.NOTRADE;

    private readonly IPhysicsEngine _engine;

    public SlopeAnalyzer(IPhysicsEngine engine)
    {
        _engine = engine;
    }

    public SIG Analyze(double slopeValue)
    {
        // Constants
        const double BASE_DECAY = 0.8;
        const double MIN_SLOPE = 0.2;
        const double HYSTERESIS = 0.90;

        double s = slopeValue;

        // 2. Adaptive Logic (Using your ported Physics Engine)
        double adxNorm = _engine.adxKinetic();
        double adaptedDecay = BASE_DECAY + (0.18 * adxNorm);

        // --- LOGIC GATE ---

        // A. RESET LOGIC
        if (s < -MIN_SLOPE) _peakPositive = 0;
        if (s > MIN_SLOPE) _peakNegative = 0;

        // B. BUY LOGIC
        double buyThreshold = (_peakPositive > 0) ? (_peakPositive * adaptedDecay) : MIN_SLOPE;

        if (s > buyThreshold)
        {
            _peakPositive = Math.Max(_peakPositive, s);
            _currentIdx = SIG.BUY;
            return SIG.BUY;
        }

        // C. SELL LOGIC
        double sellThreshold = (_peakNegative < 0) ? (_peakNegative * adaptedDecay) : -MIN_SLOPE;

        if (s < sellThreshold)
        {
            _peakNegative = Math.Min(_peakNegative, s);
            _currentIdx = SIG.SELL;
            return SIG.SELL;
        }

        // D. EXIT LOGIC with HYSTERESIS
        if (_currentIdx == SIG.BUY)
        {
            double exitLevel = (_peakPositive * adaptedDecay) * HYSTERESIS;
            if (s < exitLevel)
            {
                _currentIdx = SIG.NOTRADE;
                return SIG.CLOSE;
            }
            return SIG.BUY; // HOLD
        }

        if (_currentIdx == SIG.SELL)
        {
            double exitLevel = (_peakNegative * adaptedDecay) * HYSTERESIS;
            if (s > exitLevel)
            {
                _currentIdx = SIG.NOTRADE;
                return SIG.CLOSE;
            }
            return SIG.SELL; // HOLD
        }

        return SIG.NOTRADE;
    }
}

class SlopeSingle
{
    // --- 2. State Memory ---
    private double peakPositive;
    private double peakNegative;
    private SIG currentIdx; // Track current internal state

    private readonly IPhysicsEngine _engine;

    public SlopeSingle(IPhysicsEngine engine)
    {
        _engine = engine;
        peakPositive = 0;
        peakNegative = 0;
        currentIdx = SIG.NOSIG;
    }

    public SIG Analyze(in DTYPE slope, in double atr)
    {
        IndData indData = _engine.GetIndData();
        double pipValue = indData.PipSize;
        //const double DECAY = 0.85;

        // Normalize to Pips for scalability
        double s_pips = (pipValue > 0) ? (slope.val1 / pipValue) : slope.val1;
        double absSlopePips = Math.Abs(s_pips);

        // Define thresholds in PIPS (10 pips entry, 8 pips exit)
        double ENTRY_GATE = 7.0;
        double EXIT_GATE = ENTRY_GATE * 0.8;

        // 1. EXIT CHECK (The Floor)
        if (absSlopePips < EXIT_GATE)
        {
            if (currentIdx != SIG.NOSIG)
            {
                reset();
                return SIG.CLOSE;
            }
            return SIG.NOSIG;
        }

        // 2. ENTRY TRIGGER (The Spark)
        if (currentIdx == SIG.NOSIG)
        {
            double threshold = Math.Max(((atr / pipValue) * 0.5), 1.0);
            if (absSlopePips > ENTRY_GATE && Math.Abs(s_pips) > threshold)
            {
                currentIdx = (s_pips > 0) ? SIG.BUY : SIG.SELL;
                if (currentIdx == SIG.BUY) peakPositive = s_pips;
                else peakNegative = s_pips;
                return currentIdx;
            }
            return SIG.NOSIG;
        }

        // =================================================================
        // 3. THE DYNAMIC THERMOSTAT (Calculated on the fly)
        // =================================================================

        // Get the absolute value of our current recorded peak
        double currentAbsPeak = (currentIdx == SIG.BUY) ? peakPositive : Math.Abs(peakNegative);

        // Normalize the speed (0.0 to 1.0) based on how far above the entry gate the peak is
        double normalizedSpeed = Math.Max(0.0, Math.Min(1.0, (currentAbsPeak - ENTRY_GATE) / 18.0));

        // Calculate Dynamic Leash:
        // Slow grinds get a loose 0.65 leash. Parabolic spikes get a tight 0.90 leash.
        double DYNAMIC_DECAY = 0.65 + (0.25 * normalizedSpeed);

        // 3. HOLDING LOGIC (The Flow)
        if (currentIdx == SIG.BUY)
        {
            if (s_pips > peakPositive) peakPositive = s_pips;
            // Removed s_pips < 0 to avoid minor pullback whipsaws
            if (s_pips < (peakPositive * DYNAMIC_DECAY))
            {
                reset();
                return SIG.CLOSE;
            }
            return SIG.BUY;
        }

        if (currentIdx == SIG.SELL)
        {
            if (s_pips < peakNegative) peakNegative = s_pips;

            // Trailing Peak Exit OR Hard Reversal
            if ((s_pips > (peakNegative * DYNAMIC_DECAY)))
            {
                reset();
                return SIG.CLOSE;
            }
            return SIG.SELL;
        }

        return SIG.NOSIG;



    }
    public void reset()
    {
        peakPositive = 0;
        peakNegative = 0;
        currentIdx = SIG.NOSIG;
    }
    public SIG getStatus()
    {
        return currentIdx;
    }
}


class SlopeDouble
{

    private double m_peakRatio;  // class member
    private DateTime? m_last_bar;
    private SIG m_cached;
    private readonly IPhysicsEngine _engine;


    public SlopeDouble(IPhysicsEngine engine)
    {
        _engine = engine;
        m_peakRatio = 0;
        m_last_bar = null;
        m_cached = SIG.NOSIG;
    }
    public void reset()
    {
        m_peakRatio = 0;  // class member
        m_last_bar = null;
        m_cached = SIG.NOSIG;

    }
    public double getPeak()
    {
        return m_peakRatio;
    }
    public SIG tradeSlopeSIG(
                            in DTYPE fast,
                            in DTYPE slow,
                            in double atr,
                            ulong? magicnumber = null)
    {

        //// === 1. CACHE MANAGEMENT ===
        // This cache works agains the signal in longer time frames
        //   if(Time[0] == m_last_bar) return m_cached;
        //   m_last_bar = Time[0];


        IndData indData = _engine.GetIndData();
        double pipValue = indData.PipSize;

        if (pipValue <= 0 || slow.val1 == 0) return SIG.NOSIG;

        double fastSlope = fast.val1;
        double slowSlope = slow.val1;
        double ratio = fastSlope / slowSlope;
        double absSlowPips = Math.Abs(slowSlope) / pipValue;
        bool inTrade = (m_peakRatio > 0);

        // Commenting out the hard brake

        // Optional Safety Check: If user manually closed trade, reset memory.
        // if(inTrade && util.OrdersTotalByMagic(magicnumber) == 0) { reset(); inTrade = false; }

        //// === 2. THE EMERGENCY BRAKE (Replaces Hard Stop Loss) ===
        //// Instant closure on directional divergence.
        //   if(fastSlope * slowSlope <= 0) {
        //      if(inTrade) {
        //         reset();
        //         return SAN_SIGNAL::CLOSE;
        //      }
        //      return SAN_SIGNAL::NOSIG;
        //   }

        // === 3. THE STRUCTURAL FLOOR (Replaces Time-based Exits) ===
        const double ENTRY_GATE = 7.0;
        const double EXIT_GATE = 5.0;

        if (inTrade && absSlowPips < EXIT_GATE)
        {
            reset();
            return SIG.CLOSE;
        }
        if (!inTrade && absSlowPips < ENTRY_GATE)
        {
            return SIG.NOSIG;
        }

        // === 4. MARKET REGIME VETO (Army Strength) ===
        if (!inTrade)
        {
            double s = (atr > 0) ? Math.Abs(slowSlope) / atr : 0.0;
            double k = _engine.atrKineticNorm();
            double mod = 1.0 - 0.12 * k;

            // Do not allow ignition if ATR doesn't support the move
            if (s <= 0.09 * mod) return SIG.NOSIG;
        }

        // === 5. PURE SENTIMENT THERMOSTAT (Replaces Hard Take Profit) ===
        if (ratio > m_peakRatio) m_peakRatio = ratio;

        // Calculate dynamic leash (0.65 to 0.90) based on trend steepness
        double normalizedSpeed = Math.Max(0.0, Math.Min(1.0, (absSlowPips - ENTRY_GATE) / 18.0));
        double continuationFactor = 0.65 + (0.25 * normalizedSpeed);

        // A. The Spark (Entry)
        if (!inTrade)
        {
            if (ratio > 1.12)
            {
                // CRITICAL CLAMP: Prevent initial news-wicks from setting an impossibly high peak.
                // Without a Hard SL, a wildly high initial peak would cause an instant, painful stop-out.
                m_peakRatio = Math.Min(ratio, 2.0);

                m_cached = (fastSlope > 0) ? SIG.BUY : SIG.SELL;
                return m_cached;
            }
            return SIG.NOSIG;
        }

        // B. The Flow (Holding)
        if (ratio >= (m_peakRatio * continuationFactor))
        {
            m_cached = (fastSlope > 0) ? SIG.BUY : SIG.SELL;
            return m_cached;
        }

        // C. The Exhaustion (Exit)
        // Market sentiment has failed to sustain the structural trend.
        reset();
        return SIG.CLOSE;
    }
    public SIG analyze(
                            in DTYPE fast,
                            in DTYPE slow,
                            in double atr,
                            ulong? magicnumber = null)
    {
        return tradeSlopeSIG(fast, slow, atr, magicnumber);
    }
}
