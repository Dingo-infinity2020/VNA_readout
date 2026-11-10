Attribute VB_Name = "Module1"
Option Explicit

' E5071C internal 32-bit VBA. Source only; compile/save as .vba on instrument.
' No TCP/IP, VISA, Python or Mac connection is used by this module.
' v0.2.1: UNCALIBRATED forward S21, manually configured fixed frequency band.
Private Declare Sub Sleep Lib "kernel32" (ByVal milliseconds As Long)
Private Declare Function GetAsyncKeyState Lib "user32" (ByVal key As Long) As Integer
Private Declare Function GetTickCount Lib "kernel32" () As Long
Private mFolder As String
Private mFSO As Object
Private mAcceptance As Boolean

' Reads the configured measurement and exercises COM getters without RF changes.
Public Sub ValidateOnly()
    Dim file As String, h As Integer, result As String
    On Error GoTo ValidationFailed
    result = "VALIDATION_PASS " & Settings()
    CheckErrors
    GoTo ValidationWrite
ValidationFailed:
    result = "VALIDATION_FAIL " & CStr(Err.Number) & " " & Err.Description
ValidationWrite:
    On Error GoTo ValidationDiskFailed
    file = "D:\KID_DATA\validate_" & Format$(Now, "yyyymmdd_hhnnss") & ".txt"
    h = FreeFile
    Open file For Output As #h
    Print #h, result
    Close #h
    Exit Sub
ValidationDiskFailed:
    On Error Resume Next
    Close
End Sub

' Bounded unattended acceptance: exactly 3 sweeps at the current verified settings.
' Intended only for the known passive-filter fixture, not a KID production run.
Public Sub Acceptance3()
    mAcceptance = True
    Main
    mAcceptance = False
End Sub

Private Function Num(ByVal x As Double) As String
    ' Str uses a dot decimal separator; Trim removes its leading space.
    If x <> x Or Abs(x) > 1E+100 Then Err.Raise vbObjectError + 200, , "Non-finite or invalid numeric value."
    Num = Trim$(Str$(x))
End Function

Private Function ClockText() As String
    ' Instrument local clock; timezone/synchronization are NOT assumed.
    ClockText = Format$(Now, "yyyy-mm-dd hh:nn:ss")
End Function

Private Function EscapePressed() As Boolean
    DoEvents
    EscapePressed = (GetAsyncKeyState(27) And &H8000) <> 0
End Function

Private Function TickSeconds() As Double
    Dim t As Double
    t = CDbl(GetTickCount())
    If t < 0 Then t = t + 4294967296#
    TickSeconds = t / 1000#
End Function

Private Function Since(ByVal started As Double) As Double
    Since = TickSeconds() - started
    If Since < 0 Then Since = Since + 4294967.296
End Function

Private Sub CheckErrors()
    Dim e As Variant
    e = SCPI.SYSTem.ERRor
    If CLng(e(0)) <> 0 Then Err.Raise vbObjectError + 201, , "Instrument error: " & CStr(e(0)) & " " & CStr(e(1))
End Sub

Private Function Settings() As String
    Dim a As Double, b As Double, n As Long, bw As Double, p As Double
    a = SCPI.SENSe(1).FREQuency.STARt
    b = SCPI.SENSe(1).FREQuency.STOP
    n = SCPI.SENSe(1).SWEep.POINts
    bw = SCPI.SENSe(1).BANDwidth.RESolution
    p = SCPI.SOURce(1).POWer.LEVel.IMMediate.AMPLitude
    If a < 1000000000# Or b > 4000000000# Or b <= a Then Err.Raise vbObjectError + 202, , "Require a linear sweep inside 1-4 GHz."
    If n < 2 Or n > 1601 Then Err.Raise vbObjectError + 203, , "Require 2-1601 points."
    If p < -55 Or p > -20 Then Err.Raise vbObjectError + 204, , "Require source power between -55 and -20 dBm."
    If bw < 10 Or bw > 10000 Then Err.Raise vbObjectError + 205, , "Require IFBW 10-10000 Hz."
    If SCPI.SENSe(1).CORRection.STATe Then Err.Raise vbObjectError + 206, , "This USB prototype requires correction OFF."
    If SCPI.SENSe(1).AVERage.STATe Then Err.Raise vbObjectError + 207, , "Require averaging OFF."
    If UCase$(SCPI.SENSe(1).SWEep.TYPE) <> "LIN" Then Err.Raise vbObjectError + 208, , "Require linear frequency sweep."
    If SCPI.CALCulate(1).PARameter.COUNt <> 1 Then Err.Raise vbObjectError + 209, , "Require exactly one trace in Channel 1."
    If UCase$(SCPI.CALCulate(1).PARameter(1).DEFine) <> "S21" Then Err.Raise vbObjectError + 210, , "Channel 1 Trace 1 must be S21."
    If Not SCPI.SOURce(1).POWer.PORT.COUPle Then Err.Raise vbObjectError + 211, , "Require port power coupling ON."
    If SCPI.SOURce(1).POWer.LEVel.SLOPe.STATe Then Err.Raise vbObjectError + 212, , "Require power slope OFF."
    If SCPI.SOURce(1).POWer.PORT(1).CORRection.STATe Then Err.Raise vbObjectError + 213, , "Require source power correction OFF."
    Settings = CStr(n) & "," & Num(a) & "," & Num(b) & "," & Num(bw) & "," & Num(p) & ",UNCALIBRATED"
End Function

Private Sub AppendLog(ByVal message As String)
    Dim h As Integer
    h = FreeFile
    Open mFolder & "\status.log" For Append As #h
    Print #h, ClockText() & " " & message
    Close #h
End Sub

Private Sub OffAndVerify()
    SCPI.OUTPut.STATe = False
    SCPI.INITiate(1).CONTinuous = False
    If SCPI.OUTPut.STATe Then Err.Raise vbObjectError + 214, , "RF OFF not confirmed; check front panel."
End Sub

Public Sub Main()
    Dim root As String, folder As String, answer As String, snapshot As String
    Dim interval As Double, count As Long, k As Long, n As Long, j As Long, h As Integer
    Dim freq As Variant, z As Variant, startClock As String, endClock As String
    Dim file As String, target As String, complete As Long, armed As Boolean
    Dim started As Double, elapsed As Double, waitSeconds As Double, opc As Long, message As String
    On Error GoTo Failed
    mFolder = ""
    Set mFSO = CreateObject("Scripting.FileSystemObject")
    If mAcceptance Then
        root = "D:\KID_DATA"
    Else
        root = InputBox("Existing data folder, e.g. D:\KID_DATA (create it in Explorer first).", "VNA recorder", "")
        If Len(root) = 0 Then Exit Sub
    End If
    If Not mFSO.FolderExists(root) Then Err.Raise vbObjectError + 215, , "Destination folder does not exist."
    If Not mFSO.GetDrive(mFSO.GetDriveName(root)).IsReady Then Err.Raise vbObjectError + 216, , "Destination drive is not ready."
    If mAcceptance Then
        interval = 5
        count = 3
    Else
        answer = InputBox("Interval between sweep starts in seconds (at least 5).", "VNA recorder", "60")
        If Len(answer) = 0 Then Exit Sub
        interval = CDbl(answer)
        answer = InputBox("Number of sweeps (1-100000).", "VNA recorder", "60")
        If Len(answer) = 0 Then Exit Sub
        If CDbl(answer) <> Fix(CDbl(answer)) Then Err.Raise vbObjectError + 218, , "Sweep count must be an integer."
        count = CLng(answer)
    End If
    If interval < 5 Or interval > 86400 Then Err.Raise vbObjectError + 217, , "Interval must be 5-86400 seconds."
    If count < 1 Or count > 100000 Then Err.Raise vbObjectError + 219, , "Sweep count outside range."
    snapshot = Settings()
    CheckErrors
    If Not mAcceptance Then
      If MsgBox("UNCALIBRATED forward S21. Check chain and source power before enabling RF." & vbCrLf & _
              "CSV settings: points,start_Hz,stop_Hz,IFBW_Hz,power_dBm,calibration" & vbCrLf & snapshot & vbCrLf & _
              "Destination: " & root & vbCrLf & "Hold Esc to stop after the current sweep. Start?", vbYesNo, "USB recorder") <> vbYes Then Exit Sub
    End If
    folder = root & "\RUN_" & Format$(Now, "yyyymmdd_hhnnss")
    If mFSO.FolderExists(folder) Then Err.Raise vbObjectError + 220, , "Run folder already exists; wait a second and start again."
    mFSO.CreateFolder folder
    mFolder = folder
    h = FreeFile
    Open folder & "\run_info.txt" For Output As #h
    Print #h, "VNA_USB_RECORDER_VERSION=0.2.1"
    Print #h, "IDENTITY=" & SCPI.IEEE4882.IDN
    Print #h, "TIME_BASIS=VNA_LOCAL_UNVERIFIED"
    Print #h, "CALIBRATION=UNCALIBRATED"
    Print #h, "INTERVAL_SECONDS=" & Num(interval)
    Print #h, "REQUESTED_SWEEPS=" & CStr(count)
    Close #h
    h = FreeFile
    Open folder & "\manifest.csv" For Output As #h
    Print #h, "filename,started_local,ended_local,points,start_hz,stop_hz,ifbw_hz,power_dbm,calibration"
    Close #h
    AppendLog "STARTING"
    armed = True
    SCPI.OUTPut.STATe = False
    SCPI.INITiate(1).CONTinuous = False
    SCPI.ABORt
    SCPI.DISPlay.WINDow(1).ACTivate
    SCPI.CALCulate(1).PARameter(1).SELect
    SCPI.TRIGger.SEQuence.SCOPe = "ACT"
    SCPI.TRIGger.SEQuence.SOURce = "BUS"
    SCPI.SYSTem.ISPControl.STATe = True
    SCPI.SYSTem.ISPControl.PORT = 1
    CheckErrors
    n = SCPI.SENSe(1).SWEep.POINts
    SCPI.OUTPut.STATe = True
    For k = 1 To count
        If EscapePressed() Then Exit For
        If mFSO.FileExists(folder & "\STOP.txt") Then Exit For
        If mFSO.GetDrive(mFSO.GetDriveName(root)).FreeSpace < 100000000# Then Err.Raise vbObjectError + 221, , "Less than 100 MB free."
        If Settings() <> snapshot Then Err.Raise vbObjectError + 222, , "Measurement settings changed."
        If Not SCPI.OUTPut.STATe Then Err.Raise vbObjectError + 223, , "RF unexpectedly OFF."
        started = TickSeconds()
        startClock = ClockText()
        AppendLog "TRIGGER_REQUEST " & CStr(k)
        SCPI.DISPlay.WINDow(1).ACTivate
        SCPI.CALCulate(1).PARameter(1).SELect
        SCPI.INITiate(1).CONTinuous = True
        SCPI.TRIGger.SEQuence.SINGle
        opc = SCPI.IEEE4882.OPC
        If opc <> 1 Then Err.Raise vbObjectError + 224, , "Sweep completion not confirmed."
        SCPI.INITiate(1).CONTinuous = False
        freq = SCPI.SENSe(1).FREQuency.DATA
        z = SCPI.CALCulate(1).SELected.DATA.SDATa
        endClock = ClockText()
        CheckErrors
        If Settings() <> snapshot Then Err.Raise vbObjectError + 225, , "Settings changed during sweep."
        If UBound(freq) - LBound(freq) + 1 <> n Then Err.Raise vbObjectError + 226, , "Incomplete frequency array."
        If UBound(z) - LBound(z) + 1 <> 2 * n Then Err.Raise vbObjectError + 227, , "Incomplete complex data array."
        For j = 0 To n - 1
            If j > 0 Then
                If CDbl(freq(j)) <= CDbl(freq(j - 1)) Then Err.Raise vbObjectError + 228, , "Frequency axis invalid."
            End If
            ' CDbl rejects invalid COM data strings rather than writing a valid-looking CSV.
            answer = Num(CDbl(z(2 * j))) & "," & Num(CDbl(z(2 * j + 1)))
        Next j
        file = "sweep_" & Format$(k, "000000") & ".csv"
        target = folder & "\" & file
        h = FreeFile
        Open target & ".tmp" For Output As #h
        Print #h, "frequency_hz,s21_real,s21_imag"
        For j = 0 To n - 1
            Print #h, Num(CDbl(freq(j))) & "," & Num(CDbl(z(2 * j))) & "," & Num(CDbl(z(2 * j + 1)))
        Next j
        Close #h
        Name target & ".tmp" As target
        h = FreeFile
        Open folder & "\manifest.csv" For Append As #h
        Print #h, file & "," & startClock & "," & endClock & "," & snapshot
        Close #h
        complete = complete + 1
        AppendLog "COMMITTED " & file
        If k < count Then
            waitSeconds = interval
            elapsed = Since(started)
            If elapsed * 1.2 > waitSeconds Then waitSeconds = elapsed * 1.2
            Do
                If EscapePressed() Then GoTo Finished
                If mFSO.FileExists(folder & "\STOP.txt") Then GoTo Finished
                If Since(started) >= waitSeconds Then Exit Do
                Sleep 100
            Loop
        End If
    Next k
Finished:
    OffAndVerify
    armed = False
    AppendLog "RF_OFF_CONFIRMED"
    If complete = count Then
        AppendLog "FINISHED " & CStr(complete)
    Else
        AppendLog "STOPPED_BY_OPERATOR " & CStr(complete)
    End If
    If Not mAcceptance Then MsgBox CStr(complete) & " sweeps saved. RF OFF confirmed." & vbCrLf & folder, vbOKOnly, "USB recorder"
    Exit Sub
Failed:
    message = "STOP: " & CStr(Err.Number) & " " & Err.Description & ". No automatic retry."
    On Error Resume Next
    Close
    If armed Then
        Err.Clear
        OffAndVerify
        If Err.Number <> 0 Then
            message = message & vbCrLf & "RF OFF NOT CONFIRMED. Check front panel."
        Else
            message = message & vbCrLf & "RF OFF confirmed."
        End If
    End If
    If Len(mFolder) > 0 Then
        AppendLog message
    ElseIf mAcceptance Then
        h = FreeFile
        Open "D:\KID_DATA\acceptance_fail_" & Format$(Now, "yyyymmdd_hhnnss") & ".txt" For Output As #h
        Print #h, message
        Close #h
    End If
    If Not mAcceptance Then MsgBox message, vbCritical, "USB recorder"
End Sub
