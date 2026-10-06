#region Using directives
using System;
using System.Globalization;
using UAManagedCore;
using FTOptix.HMIProject;
using FTOptix.NetLogic;
using FTOptix.Store;
#endregion

// The bake history screen's logic. It rebuilds the grid's query whenever the search text, the date
// range or the product filter changes, computes the counts and run-time statistics, marks the
// active filter chips, and fills the detail card for the selected run (the newest one until the
// operator picks another). The grid reads every column (SELECT *): a new column needs no change here.
public class HistoryFilter : BaseNetLogic
{
    public override void Start()
    {
        store = Project.Current.Get<Store>("DataStores/OvenStore");
        history = Project.Current.Get("Model/HMI/History");
        foreach (string name in new[] { "Search", "RangeDays", "Product" })
            history.GetVariable(name).VariableChange += FilterChanged;
        history.GetVariable("SelectedRunId").VariableChange += SelectionChanged;
        first = new DelayedTask(Refresh, 2500, LogicObject);
        first.Start();
    }

    public override void Stop()
    {
        foreach (string name in new[] { "Search", "RangeDays", "Product" })
            history.GetVariable(name).VariableChange -= FilterChanged;
        history.GetVariable("SelectedRunId").VariableChange -= SelectionChanged;
        first?.Dispose();
        first = null;
    }

    private void FilterChanged(object sender, VariableChangeEventArgs e) => Refresh();

    private void SelectionChanged(object sender, VariableChangeEventArgs e)
    {
        lock (gate)
            Detail(Convert.ToInt32(e.NewValue.Value));
    }

    private void Refresh()
    {
        lock (gate)
        {
            try
            {
                string where = Where();
                history.GetVariable("GridQuery").Value =
                    $"SELECT rowid AS RunId, * FROM BakeRuns{where} ORDER BY Timestamp DESC";
                Statistics(where);
                Chips();
                store.Query($"SELECT rowid AS RunId FROM BakeRuns{where} ORDER BY Timestamp DESC LIMIT 1",
                            out string[] header, out object[,] rows);
                int newest = rows.GetLength(0) > 0 ? Convert.ToInt32(rows[0, 0]) : 0;
                var selected = history.GetVariable("SelectedRunId");
                if (Convert.ToInt32(selected.Value.Value) == newest)
                    Detail(newest);
                else
                    selected.Value = newest;
            }
            catch (Exception ex)
            {
                Log.Error("HistoryFilter", "Refreshing the bake history failed: " + ex.Message);
            }
        }
    }

    private string Where()
    {
        var parts = new System.Collections.Generic.List<string>();
        int days = Convert.ToInt32(history.GetVariable("RangeDays").Value.Value);
        if (days > 0)
            parts.Add($"EndDay >= {(int)(DateTime.UtcNow - Epoch).TotalDays - days}");
        string product = Convert.ToString(history.GetVariable("Product").Value.Value);
        if (!string.IsNullOrEmpty(product))
            parts.Add($"Product = '{product.Replace("'", "''")}'");
        string search = Convert.ToString(history.GetVariable("Search").Value.Value).Trim().ToLowerInvariant();
        if (search.Length > 0)
        {
            string escaped = search.Replace("!", "!!").Replace("%", "!%").Replace("_", "!_").Replace("'", "''");
            parts.Add($"Search LIKE '%{escaped}%' ESCAPE '!'");
        }
        return parts.Count == 0 ? "" : " WHERE " + string.Join(" AND ", parts);
    }

    private void Statistics(string where)
    {
        store.Query($"SELECT COUNT(*) AS N, AVG(RunMin) AS A, MIN(RunMin) AS Lo, MAX(RunMin) AS Hi FROM BakeRuns{where}",
                    out string[] header, out object[,] rows);
        store.Query("SELECT COUNT(*) FROM BakeRuns", out string[] h2, out object[,] all);
        int n = Convert.ToInt32(rows[0, 0]);
        history.GetVariable("CountText").Value = $"{n} OF {Convert.ToInt32(all[0, 0])} RUNS";
        history.GetVariable("StatsText").Value = n == 0 ? "No run matches the filters." :
            $"Average run {Duration(rows[0, 1])}  ·  shortest {Duration(rows[0, 2])}  ·  longest {Duration(rows[0, 3])}";
    }

    private void Chips()
    {
        int days = Convert.ToInt32(history.GetVariable("RangeDays").Value.Value);
        string product = Convert.ToString(history.GetVariable("Product").Value.Value);
        history.GetVariable("Range7Style").Value = days == 7 ? "ChipActive" : "Chip";
        history.GetVariable("Range30Style").Value = days == 30 ? "ChipActive" : "Chip";
        history.GetVariable("RangeAllStyle").Value = days <= 0 ? "ChipActive" : "Chip";
        history.GetVariable("ProductAllStyle").Value = string.IsNullOrEmpty(product) ? "ChipActive" : "Chip";
    }

    private void Detail(int runId)
    {
        try
        {
            store.Query("SELECT RunNumber, Product, DateText, StartText, EndText, RunMin, BakeMin, Units, MaxDev, Result, Timestamp "
                        + $"FROM BakeRuns WHERE rowid = {runId}", out string[] header, out object[,] rows);
            if (rows.GetLength(0) == 0)
                return;
            history.GetVariable("SelRun").Value = "RUN " + rows[0, 0];
            history.GetVariable("SelProduct").Value = $"{rows[0, 1]}  ·  {rows[0, 2]}";
            history.GetVariable("SelStart").Value = Convert.ToString(rows[0, 3]);
            history.GetVariable("SelEnd").Value = Convert.ToString(rows[0, 4]);
            history.GetVariable("SelRunTime").Value = Duration(rows[0, 5]);
            history.GetVariable("SelBake").Value = $"{Convert.ToDouble(rows[0, 6]).ToString("0.0", CultureInfo.InvariantCulture)} min";
            history.GetVariable("SelUnits").Value = Convert.ToInt32(rows[0, 7]).ToString("N0", CultureInfo.InvariantCulture);
            history.GetVariable("SelMaxDev").Value = $"{Convert.ToDouble(rows[0, 8]):0} °F";
            history.GetVariable("SelResult").Value = Convert.ToString(rows[0, 9]);
            // The curve's axis shows local time (ReferenceTimeZone = Local) and takes its bound end time as
            // written: give it the run's local wall-clock end (exp/2026-10-06-tunnel-dialecte, scenario D).
            DateTime endUtc = DateTime.SpecifyKind(Convert.ToDateTime(rows[0, 10]), DateTimeKind.Utc);
            history.GetVariable("SelEndTime").Value =
                DateTime.SpecifyKind(endUtc.ToLocalTime().AddMinutes(8), DateTimeKind.Utc);
        }
        catch (Exception ex)
        {
            Log.Error("HistoryFilter", $"Reading run {runId} failed: " + ex.Message);
        }
    }

    private static string Duration(object minutes)
    {
        int m = (int)Math.Round(Convert.ToDouble(minutes));
        return $"{m / 60} h {m % 60:00}";
    }

    private static readonly DateTime Epoch = new DateTime(2000, 1, 1, 0, 0, 0, DateTimeKind.Utc);
    private readonly object gate = new object();
    private Store store;
    private IUANode history;
    private DelayedTask first;
}
