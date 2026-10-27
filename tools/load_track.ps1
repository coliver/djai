param([string]$Query,[string]$Deck="2")
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName Microsoft.VisualBasic
$p = Get-Process mixxx | Select-Object -First 1
[Microsoft.VisualBasic.Interaction]::AppActivate($p.Id)
Start-Sleep -Milliseconds 600
[System.Windows.Forms.SendKeys]::SendWait("^f")
Start-Sleep -Milliseconds 300
[System.Windows.Forms.SendKeys]::SendWait("^a")
[System.Windows.Forms.SendKeys]::SendWait($Query)
Start-Sleep -Milliseconds 1200
[System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
Start-Sleep -Milliseconds 400
if ($Deck -eq "1") { [System.Windows.Forms.SendKeys]::SendWait("+{LEFT}") } else { [System.Windows.Forms.SendKeys]::SendWait("+{RIGHT}") }
