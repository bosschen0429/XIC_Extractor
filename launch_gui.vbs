Option Explicit
Dim shell, fs, env, root, logs, logPath, command, i, code
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
Set env = shell.Environment("PROCESS")
root = fs.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
logs = fs.BuildPath(root, "output")
If Not fs.FolderExists(logs) Then fs.CreateFolder logs
logs = fs.BuildPath(logs, "launcher")
If Not fs.FolderExists(logs) Then fs.CreateFolder logs
logPath = fs.BuildPath(logs, fs.GetTempName & ".log")
env("XIC_BACKGROUND_LAUNCH") = "1"
env("XIC_GUI_BATCH") = fs.BuildPath(root, "launch_gui.bat")
env("XIC_GUI_LOG") = logPath
command = """%XIC_GUI_BATCH%"""
For i = 0 To WScript.Arguments.Count - 1
    If InStr(WScript.Arguments(i), Chr(34)) > 0 Then
        MsgBox "Arguments cannot contain double quotes.", vbCritical, "XIC Workbench"
        WScript.Quit 1
    End If
    env("XIC_GUI_ARG_" & i) = WScript.Arguments(i)
    command = command & " ""%XIC_GUI_ARG_" & i & "%"""
Next
command = command & " > ""%XIC_GUI_LOG%"" 2>&1"
Dim runner, stream
runner = logPath & ".cmd"
Set stream = fs.CreateTextFile(runner, True, False)
stream.WriteLine "@echo off"
stream.WriteLine command
stream.Close
code = shell.Run("cmd.exe /d /v:off /s /c """ & Chr(34) & runner & Chr(34) & """", 0, True)
fs.DeleteFile runner
If code <> 0 Then
    MsgBox "XIC Workbench stopped with an error. See:" & vbCrLf & logPath, vbCritical, "XIC Workbench"
End If
WScript.Quit code
