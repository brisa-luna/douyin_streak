' Douyin streak silent launcher.
Option Explicit

Dim shell, fso, scriptDir, pythonwPath, scriptPath, logPath, logStream, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonwPath = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & _
    "\Programs\Python\Python313\pythonw.exe"
scriptPath = scriptDir & "\douyin_streak.py"
logPath = scriptDir & "\runner.log"

Set logStream = fso.OpenTextFile(logPath, 8, True)

If Not fso.FileExists(pythonwPath) Then
    logStream.WriteLine Now() & " - ERROR: pythonw.exe not found: " & pythonwPath
    logStream.Close
    WScript.Quit 1
End If

If Not fso.FileExists(scriptPath) Then
    logStream.WriteLine Now() & " - ERROR: script not found: " & scriptPath
    logStream.Close
    WScript.Quit 1
End If

logStream.WriteLine Now() & " - Scheduled run started."
logStream.Close

shell.CurrentDirectory = scriptDir
command = """" & pythonwPath & """ """ & scriptPath & """"
' Window style 0 = hidden. False = do not keep wscript resident.
shell.Run command, 0, False

Set shell = Nothing
Set fso = Nothing
