namespace Phy.Lib;

public interface IUtils
{
    public bool IsNewBar(DateTime currentBarTime);
}
public class CUtils : IUtils
{
    private DateTime _lastBar;
    private IndData _indData;

    public CUtils(IndData indData)
    {
        _indData = indData;
        _lastBar = DateTime.MinValue;
    }

    public void SetIndData(IndData data)
    {
        this._indData = data;
    }



    public bool IsNewBar(DateTime currentBarTime)
    {
        if (currentBarTime != _lastBar)
        {
            _lastBar = currentBarTime;
            return true;
        }
        return false;
    }

    //+------------------------------------------------------------------+
    //|                                                                  |
    //+------------------------------------------------------------------+
    public bool OppSignal(SIG sig1, SIG sig2)
    {
        if ((sig1 == SIG.BUY) && (sig2 == SIG.SELL))
        {
            return true;
        }
        if ((sig1 == SIG.SELL) && (sig2 == SIG.BUY))
        {
            return true;
        }
        return false;
    }

    
}


// 2. Global logging shortcut (Accessible anywhere without instantiating)
public static class Log
{
    public static void print(object message)
    {
        System.Console.WriteLine(message?.ToString());
        System.Diagnostics.Debug.WriteLine(message?.ToString());
    }
}