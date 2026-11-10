Attribute VB_Name = "KIDStorageProbe"
Option Explicit

' A storage-only preflight. Does not set RF or trigger a measurement.
Public Sub Main()
    Dim fso As Object, drive As Object, h As Integer, destination As String, message As String
    On Error GoTo Failed
    destination = "D:\KID_DATA\storage_probe_" & Format$(Now, "yyyymmdd_hhnnss") & ".txt"
    Set fso = CreateObject("Scripting.FileSystemObject")
    If fso.FileExists(destination) Then Err.Raise vbObjectError + 301, , "Probe file already exists."
    h = FreeFile
    Open destination For Output As #h
    Print #h, "STORAGE_PROBE_VERSION=0.2.1"
    Print #h, "IDENTITY=" & SCPI.IEEE4882.IDN
    Print #h, "LOCAL_CLOCK=" & Format$(Now, "yyyy-mm-dd hh:nn:ss")
    Print #h, "TYPE: 1=removable, 2=fixed, 3=network, 4=CD-ROM"
    For Each drive In fso.Drives
        If drive.IsReady Then
            Print #h, drive.DriveLetter & ":,type=" & CStr(drive.DriveType) & _
                      ",free_bytes=" & Trim$(Str$(CDbl(drive.FreeSpace))) & _
                      ",filesystem=" & CStr(drive.FileSystem)
        Else
            Print #h, drive.DriveLetter & ":,not_ready,type=" & CStr(drive.DriveType)
        End If
    Next drive
    Print #h, "PROBE_COMPLETE"
    Close #h
    Exit Sub
Failed:
    message = Err.Description
    On Error Resume Next
    Close
    MsgBox "Storage probe failed: " & message, vbCritical
End Sub
