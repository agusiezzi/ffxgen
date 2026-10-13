// Adds the registered "2D Text Box" pseudo effect to the selected layer.
// Run once, AFTER the XML has been added to PresetEffects.xml and AE relaunched.
// Then: select the effect in the timeline > Animation > Save Animation Preset...
(function () {
    var MATCHNAME = "Pseudo/YOURNAME TextBox"; // must match the XML exactly

    var comp = app.project.activeItem;
    if (!(comp && comp instanceof CompItem) || comp.selectedLayers.length === 0) {
        alert("Select one layer in an open comp first.");
        return;
    }
    app.beginUndoGroup("Add 2D Text Box pseudo effect");
    try {
        var fx = comp.selectedLayers[0].property("ADBE Effect Parade").addProperty(MATCHNAME);
        alert("Added '" + fx.name + "'.\nNow select it and use Animation > Save Animation Preset...");
    } catch (e) {
        alert("Could not add '" + MATCHNAME + "'.\nIs the XML in PresetEffects.xml and was AE restarted?\n\n" + e.toString());
    }
    app.endUndoGroup();
})();
