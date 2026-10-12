using System;

namespace Phy.Lib;

public interface IClass
{
    public void sayHello();
}
public class Class : IClass
{
    public void sayHello()
    {
        Console.WriteLine("Hello, World!");
    }
}
