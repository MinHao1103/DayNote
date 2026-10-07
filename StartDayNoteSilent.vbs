' Double-click to start DayNote without a console window flash.
' Order: installed Python first (pythonw on PATH, then the py launcher "pyw -3"), bundled runtime\pythonw.exe last.
' Company PCs may block a script that starts an executable extracted from a downloaded zip (the bundled runtime),
' while an installed Python is allowed - so the installed one is preferred (same approach as other in-house tools).
' A candidate is used only if it has tkinter and is Python 3.10+ (checked silently); pythonw shows no errors otherwise.
' Store aliases under WindowsApps are skipped unless Store / install-manager Python is really installed,
' because the bare alias opens the Microsoft Store instead of running.
' Keep this file ASCII-only: wscript.exe cannot read UTF-8 encoded scripts.
' Same order as StartDayNote.bat and SetupAutostart.bat. If nothing works, StartDayNote.bat shows the error.
Option Explicit
Dim fso, sh, base, app, exe, args, candidate
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
app = base & "\app\daynote.pyw"
sh.CurrentDirectory = base

exe = ""
args = """" & app & """"
For Each candidate In FindAllOnPath("pythonw.exe")
    If exe = "" Then
        If Usable(candidate, "") Then exe = candidate
    End If
Next
If exe = "" Then
    For Each candidate In FindAllOnPath("pyw.exe")
        If exe = "" Then
            If Usable(candidate, "-3 ") Then
                exe = candidate
                args = "-3 " & args
            End If
        End If
    Next
End If
If exe = "" Then
    If fso.FileExists(base & "\runtime\pythonw.exe") Then exe = base & "\runtime\pythonw.exe"
End If

If exe <> "" Then
    sh.Run """" & exe & """ " & args, 0, False
Else
    sh.Run """" & base & "\StartDayNote.bat""", 1, False
End If

' True when this Python can run DayNote (tkinter present, version 3.10+).
Function Usable(path, prefix)
    Usable = False
    If InStr(LCase(path), "\microsoft\windowsapps\") > 0 Then
        If Not StorePythonInstalled() Then Exit Function
    End If
    Usable = (sh.Run("""" & path & """ " & prefix & "-c ""import sys,tkinter;sys.exit(0 if sys.version_info>=(3,10) else 1)""", 0, True) = 0)
End Function

' True when a Store / install-manager Python package exists, so the WindowsApps alias really runs Python.
Function StorePythonInstalled()
    Dim apps, folder
    StorePythonInstalled = False
    apps = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Microsoft\WindowsApps"
    If Not fso.FolderExists(apps) Then Exit Function
    For Each folder In fso.GetFolder(apps).SubFolders
        If LCase(Left(folder.Name, 25)) = "pythonsoftwarefoundation." Then
            StorePythonInstalled = True
            Exit Function
        End If
    Next
End Function

' Every existing match of name in PATH, in PATH order, without duplicates.
Function FindAllOnPath(name)
    Dim found, dir, full
    Set found = CreateObject("Scripting.Dictionary")
    For Each dir In Split(sh.ExpandEnvironmentStrings("%PATH%"), ";")
        dir = Replace(Trim(dir), """", "")
        If dir <> "" Then
            full = fso.BuildPath(dir, name)
            If fso.FileExists(full) Then
                If Not found.Exists(LCase(full)) Then found.Add LCase(full), full
            End If
        End If
    Next
    FindAllOnPath = found.Items
End Function
