' Double-click to start DayNote without a console window flash.
' Same lookup order as StartDayNote.bat: runtime\pythonw.exe -> pythonw on PATH -> pyw -3 on PATH.
' Keep this file ASCII-only: wscript.exe cannot read UTF-8 encoded scripts.
' If no Python is found, StartDayNote.bat is opened to show the error message.
Option Explicit
Dim fso, sh, base, app, exe, args
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
app = base & "\app\daynote.pyw"
sh.CurrentDirectory = base

args = """" & app & """"
exe = base & "\runtime\pythonw.exe"
If Not fso.FileExists(exe) Then exe = FindOnPath("pythonw.exe")
If exe = "" Then
    exe = FindOnPath("pyw.exe")
    args = "-3 " & args
End If

If exe <> "" Then
    sh.Run """" & exe & """ " & args, 0, False
Else
    sh.Run """" & base & "\StartDayNote.bat""", 1, False
End If

Function FindOnPath(name)
    Dim dir
    FindOnPath = ""
    For Each dir In Split(sh.ExpandEnvironmentStrings("%PATH%"), ";")
        dir = Replace(Trim(dir), """", "")
        If dir <> "" Then
            If fso.FileExists(fso.BuildPath(dir, name)) Then
                FindOnPath = fso.BuildPath(dir, name)
                Exit Function
            End If
        End If
    Next
End Function
