#region Using directives
using System;
using UAManagedCore;
using FTOptix.HMIProject;
using FTOptix.NetLogic;
#endregion

// Stands in for the oven PLC, so the fixture runs with no controller connected. Every second it
// moves each zone's temperature around its setpoint, the burner outputs, the belt and the current
// run, and raises two of the three alarm conditions now and then. The same tick always gives the
// same values.
public class OvenSimulation : BaseNetLogic
{
    public override void Start()
    {
        oven = Project.Current.Get("Model/Oven");
        status = Project.Current.Get("Model/HMI/Status");
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
        tick++;
        IUANode zones = oven.Get("Zones");
        for (int z = 1; z <= ZoneCount; z++)
        {
            IUANode zone = zones.Get($"Z{z}");
            if (zone == null)
                continue;
            double sp = Convert.ToDouble(zone.GetVariable("SP").Value.Value);
            double wave = 3.0 * Math.Sin(tick / (17.0 + 3 * z) + z) + 1.5 * Math.Sin(tick / 5.3 + 2 * z);
            zone.GetVariable("PV").Value = (float)Math.Round(sp + wave);
            zone.GetVariable("Output").Value = (float)Math.Round(58 + 10 * Math.Sin(tick / 23.0 + z));
        }

        double speed = 6.8 + 0.05 * Math.Sin(tick / 40.0);
        oven.GetVariable("Belt/SpeedFtMin").Value = (float)Math.Round(speed, 1);
        oven.GetVariable("Belt/BakeTimeMin").Value = (float)Math.Round(OvenLengthFt / speed, 1);
        oven.GetVariable("Belt/Running").Value = true;

        IUAVariable units = oven.GetVariable("Run/Units");
        if (tick % 3 == 0)
            units.Value = Convert.ToInt32(units.Value.Value) + 4;

        oven.GetVariable("Alarms/ExhaustAirflowLow").Value = (tick / 45) % 4 != 3;
        oven.GetVariable("Alarms/InletDoorOpen").Value = (tick / 30) % 3 == 1;
        oven.GetVariable("Alarms/BeltSpeedDeviation").Value = false;

        string product = Convert.ToString(oven.GetVariable("Product/Name").Value.Value);
        string run = Convert.ToString(oven.GetVariable("Run/Number").Value.Value);
        status.GetVariable("Running").Value = true;
        status.GetVariable("LineText").Value = $"LINE RUNNING  ·  {product.ToUpperInvariant()}  ·  RUN {run}";
    }

    private const int ZoneCount = 5;
    private const double OvenLengthFt = 95.0;
    private PeriodicTask task;
    private IUANode oven;
    private IUANode status;
    private long tick;
}
