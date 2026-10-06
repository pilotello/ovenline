#region Using directives
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using UAManagedCore;
using FTOptix.HMIProject;
using FTOptix.NetLogic;
using FTOptix.Store;
#endregion

// The bake history. On a fresh database it writes 48 finished runs over the last 30 days, relative
// to today, so the history screen always has recent rows; each run also gets its zone temperatures
// in the logger table, one sample every two minutes, so its curve can be drawn. Only the zones in
// OvenZones.Shown are journaled and averaged.
public class RunJournal : BaseNetLogic
{
    public override void Start()
    {
        store = Project.Current.Get<Store>("DataStores/OvenStore");
        try
        {
            Seed();
        }
        catch (Exception ex)
        {
            Log.Error("RunJournal", "Seeding the bake history failed: " + ex.Message);
        }
    }

    public override void Stop()
    {
    }

    private void Seed()
    {
        if (Count("BakeRuns") > 0)
            return;
        string[] zones = OvenZones.Shown;
        var setpoints = zones.ToDictionary(z => z,
            z => Convert.ToDouble(Project.Current.GetVariable($"Model/Oven/Zones/{z}/SP").Value.Value));
        var random = new Random(20261005);

        var runColumns = new List<string> { "Timestamp", "EndDay", "RunNumber", "Product", "Started", "DateText", "StartText",
                                            "EndText", "RunMin", "BakeMin", "Units", "MaxDev", "Result", "Search" };
        runColumns.AddRange(zones.Select(z => z + "_Avg"));
        var logColumns = new List<string> { "Timestamp", "LocalTimestamp" };
        foreach (string z in zones)
        {
            logColumns.Add(z + "_PV");
            logColumns.Add(z + "_SP");
        }

        var runs = new List<object[]>();
        var samples = new List<object[]>();
        DateTime first = DateTime.UtcNow.AddDays(-30);
        double spacingMin = (30 * 24 * 60 - 180) / (double)RunCount;
        for (int i = 0; i < RunCount; i++)
        {
            DateTime start = first.AddMinutes(i * spacingMin);
            int runMin = 92 + random.Next(0, 19);
            int product = i % Products.Length;
            string spikeZone = random.NextDouble() < 0.15 ? zones[random.Next(zones.Length)] : null;
            var sums = zones.ToDictionary(z => z, z => 0.0);
            double maxDev = 0;
            int n = 0;
            for (int k = 0; k <= runMin; k += 2, n++)
            {
                DateTime at = start.AddMinutes(k);
                var row = new object[logColumns.Count];
                row[0] = at;
                row[1] = at.ToLocalTime();
                int c = 2;
                foreach (string z in zones)
                {
                    double sp = setpoints[z];
                    double warmup = k < 12 ? -2.0 * (12 - k) : 0;
                    double excursion = z == spikeZone && k > 40 && k < 56 ? 19 : 0;
                    double phase = 1.7 * Array.IndexOf(zones, z);
                    double pv = Math.Round(sp + warmup + excursion + 3.5 * Math.Sin(k / 9.0 + i + phase) + random.NextDouble() * 2 - 1);
                    sums[z] += pv;
                    if (k >= 12)
                        maxDev = Math.Max(maxDev, Math.Abs(pv - sp));
                    row[c++] = (float)pv;
                    row[c++] = (float)sp;
                }
                samples.Add(row);
            }
            DateTime end = start.AddMinutes(runMin);
            DateTime localStart = start.ToLocalTime();
            string number = $"2026-{1001 + i}";
            var run = new List<object>
            {
                end, (int)(end - Epoch).TotalDays, number, Products[product],
                localStart.ToString("MM/dd  hh:mm tt", CultureInfo.InvariantCulture),
                localStart.ToString("MM/dd/yyyy", CultureInfo.InvariantCulture),
                localStart.ToString("hh:mm tt", CultureInfo.InvariantCulture),
                end.ToLocalTime().ToString("hh:mm tt", CultureInfo.InvariantCulture),
                (float)runMin, BakeMinutes[product], runMin * 13, (float)Math.Round(maxDev),
                maxDev > 15 ? "DEVIATION" : "OK",
                (number + " " + Products[product]).ToLowerInvariant(),
            };
            run.AddRange(zones.Select(z => (object)(float)Math.Round(sums[z] / n)));
            runs.Add(run.ToArray());
        }
        // The run in progress: its last 45 minutes, every 20 s, so the live trend opens full.
        DateTime now = DateTime.UtcNow;
        for (int k = 135; k >= 1; k--)
        {
            DateTime at = now.AddSeconds(-20 * k);
            var row = new object[logColumns.Count];
            row[0] = at;
            row[1] = at.ToLocalTime();
            int c = 2;
            foreach (string z in zones)
            {
                double sp = setpoints[z];
                double phase = 1.7 * Array.IndexOf(zones, z);
                row[c++] = (float)Math.Round(sp + 3.0 * Math.Sin(k / 11.0 + phase) + 1.5 * Math.Sin(k / 4.0 + phase));
                row[c++] = (float)sp;
            }
            samples.Add(row);
        }
        Insert("OvenLogger", logColumns, samples);
        Insert("BakeRuns", runColumns, runs);
        Log.Info("RunJournal", $"Bake history seeded: {runs.Count} runs, {samples.Count} samples, zones {string.Join(",", zones)}");
    }

    private void Insert(string table, List<string> columns, List<object[]> rows)
    {
        var values = new object[rows.Count, columns.Count];
        for (int r = 0; r < rows.Count; r++)
            for (int c = 0; c < columns.Count; c++)
                values[r, c] = rows[r][c];
        store.Tables.Get<Table>(table).Insert(columns.ToArray(), values);
    }

    private int Count(string table)
    {
        store.Query($"SELECT COUNT(*) FROM {table}", out string[] header, out object[,] rows);
        return Convert.ToInt32(rows[0, 0]);
    }

    private static readonly string[] Products =
        { "Sandwich loaf 20 oz", "Hamburger bun 4 in", "Whole wheat 24 oz", "Hot dog bun 6 in" };
    private static readonly float[] BakeMinutes = { 14.0f, 9.5f, 18.0f, 9.0f };
    private static readonly DateTime Epoch = new DateTime(2000, 1, 1, 0, 0, 0, DateTimeKind.Utc);
    private const int RunCount = 48;
    private Store store;
}
