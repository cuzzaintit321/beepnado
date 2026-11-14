# BeepNado Windows agent: Windows PowerShell 5.1+, no third-party modules.
param([Parameter(Mandatory=$true)][string]$Config, [switch]$Once)
$ErrorActionPreference='Stop'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$c=Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$uri=[Uri]$c.url
if ($uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/' -or $c.token -notmatch '^[A-Za-z0-9_-]{43}$') { throw 'Invalid agent configuration' }
$endpoint=$uri.GetLeftPart([UriPartial]::Authority)+'/api/agent/metrics'
$last=$null
function Read-Metrics {
    $os=Get-CimInstance Win32_OperatingSystem
    $cpu=Get-CimInstance Win32_Processor
    $perf=Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
    $disks=@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object -First 32 | ForEach-Object {
        if ($_.Size -gt 0) { @{mount=$_.DeviceID; total_bytes=[long]$_.Size; free_bytes=[long]$_.FreeSpace; used_percent=[math]::Round(100*(1-$_.FreeSpace/$_.Size),2)} }
    })
    $services=@($c.services | Where-Object { $_ -match '^[A-Za-z0-9_.@:-]{1,120}$' } | Select-Object -First 32 | ForEach-Object {
        $name=$_
        $s=Get-Service -Name $name -ErrorAction SilentlyContinue
        $state=if (!$s) {'unknown'} elseif ($s.Status -eq 'Running') {'active'} elseif ($s.Status -eq 'Stopped') {'inactive'} else {'unknown'}
        @{name=$name;state=$state}
    })
    $processes=@($c.processes | Where-Object { $_ -match '^[A-Za-z0-9_.-]{1,120}$' } | Select-Object -First 32 | ForEach-Object {
        $name=$_
        $p=@(Get-Process -Name $name -ErrorAction SilentlyContinue)
        @{name=$name;state=if ($p.Count -gt 0) {'active'} else {'inactive'}}
    })
    $net=@(Get-CimInstance Win32_PerfRawData_Tcpip_NetworkInterface -ErrorAction SilentlyContinue)
    $io=Get-CimInstance Win32_PerfRawData_PerfDisk_PhysicalDisk -Filter "Name='_Total'" -ErrorAction SilentlyContinue
    $rx=$null;$tx=$null;$rd=$null;$wr=$null
    if ($net.Count -gt 0) {
        $rx=[long](($net | Measure-Object BytesReceivedPersec -Sum).Sum)
        $tx=[long](($net | Measure-Object BytesSentPersec -Sum).Sum)
    }
    if ($io) {$rd=[long]$io.DiskReadBytesPersec;$wr=[long]$io.DiskWriteBytesPersec}
    $now=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $uptime=[long]([DateTime]::Now-$os.LastBootUpTime).TotalSeconds
    $rebootPending=(Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
    $events=$null
    if ($c.event_logs -eq $true) {
        try {
            $events=@(Get-WinEvent -FilterHashtable @{LogName='System';Level=1;StartTime=(Get-Date).AddMinutes(-5)} -MaxEvents 100 -ErrorAction Stop).Count
        } catch {
            if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {$events=0}
        }
    }
    $result=@{platform='windows';hostname=$env:COMPUTERNAME;cpu_percent=if($perf){[double]$perf.PercentProcessorTime}else{$null};memory_percent=[math]::Round(100*(1-$os.FreePhysicalMemory/$os.TotalVisibleMemorySize),2);cpu_count=[int](($cpu|Measure-Object NumberOfLogicalProcessors -Sum).Sum);uptime_seconds=[math]::Max(0,$uptime);disks=$disks;services=$services;processes=$processes;network_rx_bytes=$rx;network_tx_bytes=$tx;disk_read_bytes=$rd;disk_write_bytes=$wr;reboot_pending=$rebootPending;critical_events_5m=$events}
    if ($script:last -and $now -gt $script:last.time -and $uptime -ge $script:last.uptime) {
        foreach ($pair in @(@('network_rx_bytes','network_rx_bps'),@('network_tx_bytes','network_tx_bps'),@('disk_read_bytes','disk_read_bps'),@('disk_write_bytes','disk_write_bps'))) {
            $key=$pair[0];$rate=$pair[1]
            if ($null -ne $result[$key] -and $null -ne $script:last.metrics[$key] -and $result[$key] -ge $script:last.metrics[$key]) {
                $result[$rate]=[math]::Round(($result[$key]-$script:last.metrics[$key])/($now-$script:last.time),2)
            }
        }
    }
    $script:last=@{time=$now;uptime=$uptime;metrics=$result}
    return $result
}
do {
    $started=Get-Date
    try {
        $body=Read-Metrics | ConvertTo-Json -Depth 6 -Compress
        $null=Invoke-RestMethod -Uri $endpoint -Method Post -Headers @{Authorization=('Bearer '+$c.token)} -ContentType 'application/json' -Body $body -TimeoutSec 20 -MaximumRedirection 0
        Write-Output 'BeepNado metrics accepted'
    } catch {
        Write-Warning ('BeepNado collection or delivery failed: '+$_.Exception.GetType().Name)
        if ($Once) { exit 1 }
    }
    if (!$Once) { Start-Sleep -Seconds ([math]::Max(1,60-[int]((Get-Date)-$started).TotalSeconds)) }
} while (!$Once)
