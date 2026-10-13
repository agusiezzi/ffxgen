// Applies a chosen .ffx to the selected layer and reports what landed.
// Use this to test generated pseudo effects on a machine that has NEVER
// had their matchname in PresetEffects.xml.
(function () {
    var comp = app.project.activeItem;
    if (!(comp && comp instanceof CompItem) || comp.selectedLayers.length === 0) {
        alert("Select one layer in an open comp first."); return;
    }
    var f = File.openDialog("Choose a generated .ffx", "*.ffx");
    if (!f) return;
    var layer = comp.selectedLayers[0];
    app.beginUndoGroup("Apply " + f.name);
    try {
        layer.applyPreset(f);
        var fx = layer.property("ADBE Effect Parade");
        var last = fx.property(fx.numProperties);
        var lines = ["Applied: " + last.name + "  (" + last.matchName + ")", ""];
        function walk(p, ind) {
            for (var i = 1; i <= p.numProperties; i++) {
                var q = p.property(i);
                var v = "";
                try { if (q.propertyType === PropertyType.PROPERTY) v = " = " + q.value; } catch (e) {}
                lines.push(ind + q.name + v);
                if (q.propertyType !== PropertyType.PROPERTY) walk(q, ind + "    ");
            }
        }
        walk(last, "");
        alert(lines.join("\n"));
    } catch (e) {
        alert("applyPreset failed: " + e.toString());
    }
    app.endUndoGroup();
})();
