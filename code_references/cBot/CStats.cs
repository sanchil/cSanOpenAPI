using System.Reflection.Metadata;
using MathNet.Numerics.Statistics;
using MathNet.Numerics.LinearAlgebra;

namespace Phy.Lib;

public interface IStats
{
    public (double mean, double stdDev, double zScore) GetDistribution(double[] values, int shift = 0);
    public double zScore(in double inpVal, in double mean, in double std);
    public double CalculateKurtosis(double[] values, int N, int shift = 0);

    public int HistogramMagnitude(double[] values, int N = 20, int bins = 5, double minThresh = 0.2);
    public double CalculateSkewness(double[] values, int N, int shift = 0);
    // public double[] slopesVal(
    //     in double[] sig,
    //     in int SLOPEDENOM = 3,
    //     in int SLOPEDENOM_WIDE = 5,
    //     in int shift = 1);

    public DTYPE slopesVal(
        in double[] sig,
        in int SLOPEDENOM = 3,
        in int SLOPEDENOM_WIDE = 5,
        in int shift = 0);

    public DTYPE slopesVal_v2(double[] smoothedSeries, int shift = 1);    

    public double[] slopeRange_v2(in double[] sig, in IndData indData, int range = 15, int slopeDenom = 3, int shift = 1);


}

public class CStats : IStats
{
    private IndData _indData;
    private readonly IUtils _utils;

    public CStats(IndData indData, IUtils utils)
    {
        this._indData = indData;
        this._utils = utils;
    }

    public void SetIndData(IndData data)
    {
        this._indData = data;
    }

    // Implementation of GetDistribution
    public (double mean, double stdDev, double zScore) GetDistribution(double[] values, int shift = 0)
    {
        // Safety check for empty or tiny arrays
        if (values == null || values.Length < 2)
            return (0, 0, 0);

        // Using MathNet Extension Methods
        double mean = values.Mean();
        double stdDev = values.StandardDeviation();

        // Calculate Z-Score: (Current - Average) / StdDev
        // 1e-12 check prevents division by zero in flat markets
        double currentVal = values[shift];
        double zScore = (stdDev > 1e-12) ? (currentVal - mean) / stdDev : 0;

        return (mean, stdDev, zScore);
    }
    public double zScore(in double inpVal, in double mean, in double std)
    {
        return ((inpVal - mean) / (std + 0.000000001));
    }

    public DTYPE slopesVal(
            in double[] sig,
            in int SLOPEDENOM = 3,
            in int SLOPEDENOM_WIDE = 5,
            in int shift = 0)
    {
        // double[] slopes = new double[4];
        DTYPE slopes = new DTYPE(); // Placeholder for any struct-based calculations if needed
        double ppip = _indData.PipSize;
        if (ppip == 0.0) ppip = 0.0001; // Default to a standard pip value if not provided
        slopes.val1 = (sig[shift] - sig[shift + SLOPEDENOM]) / (SLOPEDENOM * ppip);
        slopes.val2 = (sig[shift] - sig[shift + SLOPEDENOM_WIDE]) / (SLOPEDENOM_WIDE * ppip);
        slopes.val3 = (sig[shift] - sig[sig.Length - 1]) / ((sig.Length - 1) * ppip);

        // Slope difference as a measure of momentum change
        slopes.val4 = slopes.val1 - slopes.val2;

        return slopes;
    }




    // Change return type to bool for error checking
    public double[] slopeRange_v2(in double[] sig, in IndData indData, int range = 15, int slopeDenom = 3, int shift = 1)
    {

        double[] outputArr = new double[range + 5];
        // Safety Check: Not enough data in source
        if (sig.Length < (range + shift + slopeDenom))
        {
            return outputArr;
        }


        //// Optimization: Get PipValue once per call, not inside the loop
        //double pipVal = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
        //long digits = SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

        //// Adjust for 3/5 digit brokers
        //if(digits == 3 || digits == 5) pipVal *= 10;
        //if(pipVal == 0) pipVal = 0.0001;

        double pipValue = indData.PipSize;
        if (pipValue <= 0)
            pipValue = indData.Point * 10; // Fallback to a default pip value if not set

        for (int i = 0; i < range; i++)
        {
            int idx = shift + i;
            // Slope logic
            double rawVal = (sig[idx] - sig[idx + slopeDenom]) / ((double)slopeDenom * pipValue);
            outputArr[i] = rawVal;
        }
        return outputArr; // Success
    }


    public DTYPE slopesVal_v2(double[] smoothedSeries, int shift = 1)
    {
        // Explicitly initialize your classic struct type
        DTYPE dt = new DTYPE();

        // Safety boundary check
        if (smoothedSeries == null || smoothedSeries.Length < shift + 3) return dt;

        // Use your pre-configured IndData pip structure
        double pipSize = _indData.PipSize <= 0 ? 0.0001 : _indData.PipSize;

        // Step 1: Calculate Velocity 1 (Current Closed Bar Speed in Pips)
        // Distance traveled over 1 bar interval (e.g., from Bar 2 to Bar 1)
        double v1 = (smoothedSeries[shift] - smoothedSeries[shift + 1]) / pipSize;

        // Step 2: Calculate Velocity 2 (Previous Closed Bar Speed in Pips)
        // Distance traveled over the previous 1 bar interval (e.g., from Bar 3 to Bar 2)
        double v2 = (smoothedSeries[shift + 1] - smoothedSeries[shift + 2]) / pipSize;

        // Step 3: Pure, dimensionally sound sequential acceleration
        double primitiveAcceleration = v1 - v2;

        // Assign back to your signature structural format
        dt.val1 = Math.Round(v1, 4);                    // Short instantaneous speed
        dt.val2 = Math.Round(v2, 4);                    // Lagging historical benchmark speed
        dt.val3 = Math.Round(primitiveAcceleration, 4); // True structural acceleration change

        return dt;
    }

    public double CalculateKurtosis(double[] values, int N, int shift = 0)
    {
        if (values.Length < N + shift || N < 4) return 0.0;

        double mean = 0;
        for (int i = shift; i < N + shift; i++) mean += values[i];
        mean /= N;

        double s2 = 0, s4 = 0;
        for (int i = shift; i < N + shift; i++)
        {
            double dev = values[i] - mean;
            s2 += dev * dev;
            s4 += dev * dev * dev * dev;
        }

        double variance = s2 / N;
        if (variance < 1.0e-16) return 0.0;

        double rawKurt = (s4 / N) / (variance * variance);
        return rawKurt - 3.0; // Excess Kurtosis
    }

    public double CalculateSkewness(double[] values, int N, int shift = 0)
    {
        if (values.Length < N + shift || N <= 1) return 0.0;

        double meanVal = 0.0;
        for (int i = shift; i < N + shift; i++) meanVal += values[i];
        meanVal /= N;

        double variance = 0.0, skew = 0.0;
        for (int i = shift; i < N + shift; i++)
        {
            double dev = values[i] - meanVal;
            variance += dev * dev;
            skew += dev * dev * dev;
        }
        variance /= N;
        double stdDev = Math.Sqrt(variance);

        if (stdDev < 1.0e-16) return 0.0;
        return (skew / N) / (stdDev * stdDev * stdDev);
    }

    public int HistogramMagnitude(double[] values, int N = 20, int bins = 5, double minThresh = 0.2)
    {
        if (values.Length < N) return -1;

        double maxVal = 0.0;
        foreach (var v in values) // Clean C# foreach loop
        {
            double absV = Math.Abs(v);
            if (absV > maxVal) maxVal = absV;
        }

        if (maxVal < minThresh) maxVal = minThresh;

        int[] counts = new int[bins]; // C# automatically initializes to 0
        double step = maxVal / bins;
        if (step == 0) return 0;

        for (int i = 0; i < N; i++)
        {
            int binIdx = (int)(Math.Abs(values[i]) / step);
            if (binIdx >= bins) binIdx = bins - 1;
            counts[binIdx]++;
        }

        int maxCount = 0;
        int domBin = -1;
        for (int b = 0; b < bins; b++)
        {
            if (counts[b] > maxCount)
            {
                maxCount = counts[b];
                domBin = b;
            }
        }

        return (maxCount < N * 0.4) ? -1 : domBin;
    }



}