#region Using directives
using System;
using System.Globalization;
using System.IO;
using System.Text;
using UAManagedCore;
using FTOptix.HMIProject;
using FTOptix.NetLogic;
using FTOptix.Store;
using FTOptix.Core;
#endregion

// "Export CSV" on the bake history screen: writes the runs the grid shows, with every column the
// query returns, to ApplicationFiles/exports/. Comma-separated, UTF-8, RFC 4180 quoting.
public class CsvExport : BaseNetLogic
{
    [ExportMethod]
    public void Export()
    {
        IUANode history = Project.Current.Get("Model/HMI/History");
        try
        {
            var store = Project.Current.Get<Store>("DataStores/OvenStore");
            string query = Convert.ToString(history.GetVariable("GridQuery").Value.Value);
            store.Query(query, out string[] header, out object[,] rows);
            var text = new StringBuilder();
            text.Append(string.Join(",", header)).Append("\r\n");
            for (int r = 0; r < rows.GetLength(0); r++)
            {
                var cells = new string[header.Length];
                for (int c = 0; c < header.Length; c++)
                    cells[c] = Quote(Convert.ToString(rows[r, c], CultureInfo.InvariantCulture));
                text.Append(string.Join(",", cells)).Append("\r\n");
            }
            string folder = ResourceUri.FromApplicationRelativePath("exports").Uri;
            Directory.CreateDirectory(folder);
            string file = Path.Combine(folder, $"bake-history-{DateTime.Now:yyyyMMdd-HHmmss}.csv");
            File.WriteAllText(file, text.ToString(), new UTF8Encoding(false));
            history.GetVariable("ExportText").Value = $"Exported {rows.GetLength(0)} runs to {Path.GetFileName(file)}";
            Log.Info("CsvExport", $"Exported {rows.GetLength(0)} runs to {file}");
        }
        catch (Exception ex)
        {
            history.GetVariable("ExportText").Value = "Export failed: " + ex.Message;
            Log.Error("CsvExport", "Export failed: " + ex.Message);
        }
    }

    private static string Quote(string s) =>
        s.IndexOfAny(new[] { ',', '"', '\r', '\n' }) >= 0 ? "\"" + s.Replace("\"", "\"\"") + "\"" : s;
}
