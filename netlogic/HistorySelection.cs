#region Using directives
using System;
using UAManagedCore;
using FTOptix.HMIProject;
using FTOptix.NetLogic;
#endregion

// Lives under the bake history grid: when the operator selects a row, it publishes that run's id
// to Model/HMI/History/SelectedRunId, which HistoryFilter turns into the detail card and the curve.
public class HistorySelection : BaseNetLogic
{
    public override void Start()
    {
        selected = Owner.GetVariable("UISelectedItem");
        if (selected != null)
            selected.VariableChange += Changed;
    }

    public override void Stop()
    {
        if (selected != null)
            selected.VariableChange -= Changed;
    }

    private void Changed(object sender, VariableChangeEventArgs e)
    {
        try
        {
            var id = e.NewValue.Value as NodeId;
            if (id == null)
                return;
            IUAVariable runId = InformationModel.Get(id)?.GetVariable("RunId");
            if (runId != null)
                Project.Current.GetVariable("Model/HMI/History/SelectedRunId").Value = Convert.ToInt32(runId.Value.Value);
        }
        catch (Exception ex)
        {
            Log.Warning("HistorySelection", "Selection not applied: " + ex.Message);
        }
    }

    private IUAVariable selected;
}
