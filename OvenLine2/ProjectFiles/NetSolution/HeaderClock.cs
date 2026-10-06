#region Using directives
using System;
using System.Globalization;
using UAManagedCore;
using FTOptix.HMIProject;
using FTOptix.NetLogic;
#endregion

// The header clock, written as text every second (a text works on every Runtime platform).
public class HeaderClock : BaseNetLogic
{
    public override void Start()
    {
        header = Project.Current.Get("Model/HMI/Header");
        task = new PeriodicTask(Tick, 1000, LogicObject);
        task.Start();
    }

    public override void Stop()
    {
        task?.Dispose();
        task = null;
    }

    private void Tick()
    {
        DateTime now = DateTime.Now;
        header.GetVariable("ClockDate").Value = now.ToString("MM/dd/yyyy", CultureInfo.InvariantCulture);
        header.GetVariable("ClockTime").Value = now.ToString("hh:mm:ss tt", CultureInfo.InvariantCulture);
    }

    private PeriodicTask task;
    private IUANode header;
}
