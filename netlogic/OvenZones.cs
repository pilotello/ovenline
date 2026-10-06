// The oven zones this HMI shows. The simulation, standing in for the PLC, drives every zone under
// Model/Oven/Zones; the run journal logs and averages only the zones listed here.
public static class OvenZones
{
    public static readonly string[] Shown = { "Z1", "Z2", "Z3", "Z4" };
}
