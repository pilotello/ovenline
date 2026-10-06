#region Using directives
using System;
using UAManagedCore;
using FTOptix.HMIProject;
using FTOptix.NetLogic;
#endregion

// The footer's ALARMS button and the alarm list: counts the active alarm conditions every second,
// picks the button style (red when anything is active) and writes each condition's state.
public class AlarmBadge : BaseNetLogic
{
    public override void Start()
    {
        sources = Project.Current.Get("Model/Oven/Alarms");
        badge = Project.Current.Get("Model/HMI/Alarms");
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
        int active = 0;
        foreach (string name in Conditions)
        {
            bool on = Convert.ToBoolean(sources.GetVariable(name).Value.Value);
            if (on)
                active++;
            badge.GetVariable(name + "State").Value = on ? "ACTIVE" : "NORMAL";
        }
        badge.GetVariable("Count").Value = active;
        badge.GetVariable("ButtonText").Value = active > 0 ? $"ALARMS  {active}" : "ALARMS";
        badge.GetVariable("ButtonStyle").Value = active > 0 ? "AlarmActive" : "AlarmIdle";
    }

    private static readonly string[] Conditions = { "ExhaustAirflowLow", "InletDoorOpen", "BeltSpeedDeviation" };
    private PeriodicTask task;
    private IUANode sources;
    private IUANode badge;
}
