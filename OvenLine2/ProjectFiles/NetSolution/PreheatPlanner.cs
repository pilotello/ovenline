#region Using directives
using System;
using System.Globalization;
using UAManagedCore;
using FTOptix.HMIProject;
using FTOptix.NetLogic;
#endregion

// The weekly preheat schedule. Day1 is Monday, Day7 Sunday. For each day it keeps the start time on
// a quarter hour within the day, writes the start and "ready by" texts (start plus the warm-up
// time), marks today, and computes the next enabled preheat.
public class PreheatPlanner : BaseNetLogic
{
    public override void Start()
    {
        schedule = Project.Current.Get("Model/HMI/Schedule");
        for (int d = 1; d <= 7; d++)
        {
            IUANode day = schedule.Get($"Day{d}");
            day.GetVariable("DayName").Value = Names[d - 1];
            day.GetVariable("StartMinutes").VariableChange += Changed;
            day.GetVariable("Enabled").VariableChange += Changed;
        }
        TimeSpan offset = TimeZoneInfo.Local.GetUtcOffset(DateTime.Now);
        schedule.GetVariable("TimeZoneText").Value =
            $"LOCAL TIME  ·  UTC{(offset < TimeSpan.Zero ? "-" : "+")}{offset.Duration():hh\\:mm}";
        task = new PeriodicTask(Update, 30000, LogicObject);
        task.Start();
        Update();
    }

    public override void Stop()
    {
        task?.Dispose();
        task = null;
        for (int d = 1; d <= 7; d++)
        {
            IUANode day = schedule.Get($"Day{d}");
            day.GetVariable("StartMinutes").VariableChange -= Changed;
            day.GetVariable("Enabled").VariableChange -= Changed;
        }
    }

    private void Changed(object sender, VariableChangeEventArgs e) => Update();

    private void Update()
    {
        lock (gate)
        {
            int warmup = Convert.ToInt32(schedule.GetVariable("WarmupMin").Value.Value);
            DateTime now = DateTime.Now;
            int today = ((int)now.DayOfWeek + 6) % 7 + 1;
            DateTime? next = null;
            for (int d = 1; d <= 7; d++)
            {
                IUANode day = schedule.Get($"Day{d}");
                IUAVariable startVar = day.GetVariable("StartMinutes");
                int start = Convert.ToInt32(startVar.Value.Value);
                int normal = ((start % 1440) + 1440) % 1440;
                if (normal != start)
                    startVar.Value = normal;
                bool enabled = Convert.ToBoolean(day.GetVariable("Enabled").Value.Value);
                day.GetVariable("StartText").Value = Clock(normal);
                day.GetVariable("ReadyText").Value = enabled ? "Ready by " + Clock(normal + warmup) : "Preheat off";
                day.GetVariable("IsToday").Value = d == today;
                if (enabled)
                {
                    int ahead = (d - today + 7) % 7;
                    DateTime at = now.Date.AddDays(ahead).AddMinutes(normal);
                    if (at <= now)
                        at = at.AddDays(7);
                    if (next == null || at < next)
                        next = at;
                }
            }
            schedule.GetVariable("NextStartText").Value = next == null ? "No preheat scheduled" :
                "Next preheat " + next.Value.ToString("dddd hh:mm tt", CultureInfo.InvariantCulture).ToUpperInvariant();
        }
    }

    private static string Clock(int minutes)
    {
        int m = ((minutes % 1440) + 1440) % 1440;
        return new DateTime(2000, 1, 1).AddMinutes(m).ToString("hh:mm tt", CultureInfo.InvariantCulture);
    }

    private static readonly string[] Names = { "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY" };
    private readonly object gate = new object();
    private IUANode schedule;
    private PeriodicTask task;
}
