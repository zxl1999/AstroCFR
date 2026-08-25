param(
    [Parameter(Mandatory = $true)]
    [string]$DocumentPath
)

$ErrorActionPreference = 'Stop'
$resolvedPath = (Resolve-Path -LiteralPath $DocumentPath).Path

# UnicodeMath is intentionally used here.  Construct the few non-ASCII
# symbols from code points so Windows PowerShell 5.1 cannot misread this UTF-8
# source file under a Chinese ANSI code page.  The terminal #(n) marker is the
# Word equation-editor convention that places equation numbers at the right
# margin while keeping the expression centred.
$sum = [char]0x2211
$member = [char]0x2208
$le = [char]0x2264
$chi = [char]0x03c7
$sigma = [char]0x03c3
$hat = [char]0x0302
$xhat = "x$hat"
$mhat = "m$hat"
$equations = @(
    [PSCustomObject]@{
        Marker = '[[WORD_INLINE_DECISION_VECTOR]]'
        Linear = 'z_b=(1-C_b,1-R_(dense,b),E_(pos,b),E_(mag,b),T_b,M_b)'
    },
    [PSCustomObject]@{
        Marker = '[[WORD_NATIVE_EQUATION_1]]'
        Linear = "R_(dense)=N_(dense)^(-1) ${sum}_(i${member}D) 1[min_j ||${xhat}_j-x_i||_2${le}r_(match)]#(1)"
    },
    [PSCustomObject]@{
        Marker = '[[WORD_NATIVE_EQUATION_2]]'
        Linear = "${chi}^2_(G)=${sum}_(p${member}G)[I_p-B_p-${sum}_(k${member}G)f_k P_p(x_k,y_k)]^2/${sigma}_p^2#(2)"
    },
    [PSCustomObject]@{
        Marker = '[[WORD_NATIVE_EQUATION_3]]'
        Linear = "E_(pos)=s[N^(-1)${sum}_i ||${xhat}_i-x_i||_2^2]^(1/2), E_(mag)=[N^(-1)${sum}_i(${mhat}_i-m_i)^2]^(1/2)#(3)"
    }
)

$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($resolvedPath, $false, $false)

    foreach ($equation in $equations) {
        $range = $document.Content
        $finder = $range.Find
        $finder.ClearFormatting()
        $finder.Replacement.ClearFormatting()
        $finder.Text = $equation.Marker
        $finder.Forward = $true
        $finder.Wrap = 0  # wdFindStop
        if (-not $finder.Execute()) {
            throw "Equation placeholder not found: $($equation.Marker)"
        }
        $start = $range.Start
        $range.Text = $equation.Linear
        $mathRange = $document.Range($start, $start + $equation.Linear.Length)
        # Word's OMaths.Add returns a Range; BuildUp belongs to that range's
        # OMath collection, not to the returned Range object itself.
        $null = $document.OMaths.Add($mathRange)
        $mathRange.OMaths.BuildUp()
    }

    # Hide non-printing characters and layout guides for the default document
    # view: spaces, paragraph marks, section-break labels, crop marks, and
    # text-boundary corners should never be part of the reading view.
    $view = $word.ActiveWindow.View
    $view.ShowAll = $false
    $view.ShowSpaces = $false
    $view.ShowParagraphs = $false
    $view.ShowTabs = $false
    $view.ShowOptionalBreaks = $false
    $view.ShowTextBoundaries = $false
    $view.ShowCropMarks = $false
    $document.Save()
}
finally {
    if ($document -ne $null) { $document.Close() }
    if ($word -ne $null) { $word.Quit() }
}

# Word stores the paragraph-mark toggle as a per-user preference.  Clear it
# after Word has exited so future document openings do not restore the dots
# and paragraph/section-break symbols.
Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Office\16.0\Word\Options' -Name ShowParaMarks -Type DWord -Value 0
