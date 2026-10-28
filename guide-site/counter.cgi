#!/usr/bin/perl
#
# counter.cgi  汎用アクセスカウンター（複数ページ対応・管理画面つき）  v2
#
# ■ 呼び出し方（page= にページごとのタグを付ける）
#   counter.cgi?page=kagimemo            … 1回数えて、結果をJSONで返す
#   counter.cgi?page=kagimemo&mode=view  … 数えずに現在の値だけ返す
#   counter.cgi?page=kagimemo&fmt=text   … 合計数だけをテキストで返す
#   counter.cgi?page=kagimemo&fmt=svg    … 画像（SVG）で返す。<img src="..."> でJavaScriptなしでも使える
#
#   JSONの例: {"page":"kagimemo","total":1234,"today":12,"yesterday":34,"counted":1}
#
# ■ 管理画面
#   counter.cgi?mode=admin  … 全ページの一覧、ページごとの月別・日別の記録
#   使うには、データフォルダに admin_password.txt を作り、1行目に管理用パスワードを書きます
#   （例: counter_data/admin_password.txt）。このファイルが無いと管理画面は使えません。
#   データフォルダは .htaccess でブラウザから読めないので、パスワードが外から見えることはありません。
#
# ■ データ
#   ページごとに $DATA_DIR/<タグ>.dat に保存します（1ページ1ファイル）。
#   タグに使える文字は 英数字・ハイフン・アンダースコア（64文字まで）。
#   日別の件数も同じファイルに記録します（1日1行・約20バイト）。月別は日別から集計します。
#   同じ人（IPアドレス）が $REPEAT_SEC 秒以内に再訪しても数えません。
#   IPアドレスはそのまま保存せず、ハッシュ値（一部）だけを保存します。
#
# ■ 設置
#   1. このファイルと counter_data フォルダ（.htaccess 入り）をサーバーにアップロード
#   2. counter.cgi のパーミッションを 755（サーバーの指定があればそれに従う）
#   3. counter_data フォルダを 755（書き込めない場合は 707 か 777）
#   4. 1行目のPerlのパスがサーバーと違う場合は書き換える（例: #!/usr/local/bin/perl）
#   5. 管理画面を使う場合は counter_data/admin_password.txt を作る（パーミッション 600 か 644）
#
use strict;
use warnings;
use Fcntl qw(:flock O_RDWR O_CREAT O_RDONLY);
use Digest::MD5 qw(md5_hex);
use Digest::SHA qw(sha256_hex hmac_sha256_hex);

# ===== 設定 =====
my $DATA_DIR    = './counter_data';   # データの保存先（このCGIからの相対パス）
my %ALLOW       = ();                 # 使うページを限定する場合: ( 'kagimemo' => 1, 'top' => 1 )
my $MAX_PAGES   = 100;                # %ALLOW が空のとき、自動で作れるページ数の上限（いたずら対策）
my $REPEAT_SEC  = 3600;               # 同じIPからの再訪を数えない秒数（0で毎回数える）
my $IP_KEEP     = 300;                # 再訪判定のために覚えておくIPの件数（ページごと）
my $HASH_SALT   = 'change-this-salt'; # IPハッシュ・ログイン用の文字列（設置時に任意の文字列へ変更推奨）
my $TZ_OFFSET   = 9 * 3600;           # 日付の区切り（日本時間）
my @CORS_ORIGIN = ('*');              # ほかのドメインのページから呼ぶ場合の許可。限定するなら ('https://example.com')
my $ADMIN_PASS_FILE = 'admin_password.txt'; # 管理用パスワードのファイル名（$DATA_DIR の中）
my $ADMIN_HOURS = 12;                 # 管理画面のログインを保つ時間
# ===============

my %q = parse_query($ENV{QUERY_STRING} || '');
my $mode = defined $q{mode} ? $q{mode} : 'count';

if ($mode eq 'admin' || $mode eq 'login' || $mode eq 'logout') { admin_main(); exit; }

my $page = defined $q{page} ? $q{page} : '';
my $fmt  = defined $q{fmt}  ? $q{fmt}  : 'json';

if (!valid_tag($page))         { fail(400, 'invalid page tag'); }
if (%ALLOW && !$ALLOW{$page}) { fail(404, 'unknown page tag'); }

unless (-d $DATA_DIR) { mkdir $DATA_DIR, 0755 or fail(500, 'cannot create data dir'); }
my $file = "$DATA_DIR/$page.dat";

if (!-e $file && !%ALLOW) {
    fail(403, 'too many pages') if scalar(list_pages()) >= $MAX_PAGES;
}
if (!-e $file && $mode eq 'view') {
    output({ total => 0, today => 0, yesterday => 0, counted => 0 });
    exit;
}

# ---- 読み込み・更新（ファイルロック付き） ----
my $now = time;
my $lock = ($mode eq 'view') ? LOCK_SH : LOCK_EX;
my $fh;
if ($mode eq 'view') { sysopen($fh, $file, O_RDONLY) or fail(500, 'cannot open data file'); }
else                 { sysopen($fh, $file, O_RDWR | O_CREAT, 0644) or fail(500, 'cannot open data file'); }
flock($fh, $lock) or fail(500, 'cannot lock data file');
binmode $fh;

my ($d, $day, $ips) = read_data($fh, $now);

my $counted = 0;
if ($mode ne 'view') {
    my $ip = substr(md5_hex($HASH_SALT . ($ENV{REMOTE_ADDR} || '')), 0, 12);
    my $seen = 0;
    my @keep;
    if ($REPEAT_SEC > 0) {
        for my $e (@$ips) {
            my ($h, $t) = split /:/, $e, 2;
            next unless $h && defined $t && $t =~ /\A\d+\z/;
            next if $now - $t >= $REPEAT_SEC;        # 期限切れは捨てる
            $seen = 1 if $h eq $ip;                  # 最初に数えた時刻のまま残す（期限は延ばさない）
            push @keep, "$h:$t";
        }
        push @keep, "$ip:$now" unless $seen;
        @keep = @keep[-$IP_KEEP .. -1] if @keep > $IP_KEEP;
    }
    unless ($seen) { $d->{total}++; $d->{today}++; $counted = 1; }
    $day->{ $d->{date} } = $d->{today};

    seek($fh, 0, 0);
    truncate($fh, 0);
    print {$fh} "total=$d->{total}\n", "date=$d->{date}\n", "today=$d->{today}\n", "yesterday=$d->{yesterday}\n";
    print {$fh} "day=$_:$day->{$_}\n" for sort keys %$day;
    print {$fh} "ip=$_\n" for @keep;
}
close $fh;   # ロックも解除される

output({ total => $d->{total}, today => $d->{today}, yesterday => $d->{yesterday}, counted => $counted });
exit;

# =====================================================================
#  データ
# =====================================================================

# データファイルを読み、日付の切り替わりを反映した値を返す
sub read_data {
    my ($fh, $now) = @_;
    my %d = (total => 0, date => '', today => 0, yesterday => 0);
    my (%day, @ips);
    while (my $line = <$fh>) {
        $line =~ s/[\r\n]+\z//;
        my ($k, $v) = split /=/, $line, 2;
        next unless defined $v;
        if    ($k eq 'ip')  { push @ips, $v; }
        elsif ($k eq 'day') { my ($dt, $n) = split /:/, $v, 2; $day{$dt} = int($n || 0) if $dt =~ /\A\d{4}-\d\d-\d\d\z/; }
        else                { $d{$k} = $v; }
    }
    $d{$_} = int($d{$_} || 0) for qw(total today yesterday);

    my $today = ymd($now);
    if ($d{date} ne $today) {
        # 前回の日の件数を日別記録に残してから切り替える（旧形式のファイルからの移行も兼ねる）
        $day{ $d{date} } = $d{today} if $d{date} =~ /\A\d{4}-\d\d-\d\d\z/ && !exists $day{ $d{date} };
        $d{yesterday} = ($d{date} eq ymd($now - 86400)) ? $d{today} : 0;
        $d{today} = 0;
        $d{date}  = $today;
    }
    return (\%d, \%day, \@ips);
}

sub load_page {
    my ($tag) = @_;
    my $f = "$DATA_DIR/$tag.dat";
    open(my $fh, '<', $f) or return;
    flock($fh, LOCK_SH);
    my ($d, $day) = read_data($fh, time);
    close $fh;
    $day->{ $d->{date} } = $d->{today} if $d->{today} || exists $day->{ $d->{date} };
    return ($d, $day);
}

sub list_pages {
    opendir(my $dh, $DATA_DIR) or return ();
    my @p = sort map { /\A([A-Za-z0-9_-]{1,64})\.dat\z/ ? $1 : () } readdir $dh;
    closedir $dh;
    return @p;
}

sub valid_tag { return defined $_[0] && $_[0] =~ /\A[A-Za-z0-9_-]{1,64}\z/; }

sub ymd {
    my @t = gmtime($_[0] + $TZ_OFFSET);
    return sprintf('%04d-%02d-%02d', $t[5] + 1900, $t[4] + 1, $t[3]);
}

# =====================================================================
#  カウンターの出力
# =====================================================================

sub headers {
    my ($type, $status, $extra) = @_;
    my $h = '';
    $h .= "Status: $status\r\n" if $status;
    $h .= "Content-Type: $type\r\n";
    $h .= "Cache-Control: no-store, max-age=0\r\n";
    $h .= "X-Content-Type-Options: nosniff\r\n";
    $h .= $extra if $extra;
    if (!$extra && @CORS_ORIGIN) {
        my $origin = $ENV{HTTP_ORIGIN} || '';
        if ($CORS_ORIGIN[0] eq '*') { $h .= "Access-Control-Allow-Origin: *\r\n"; }
        elsif (grep { $_ eq $origin } @CORS_ORIGIN) { $h .= "Access-Control-Allow-Origin: $origin\r\nVary: Origin\r\n"; }
    }
    print $h, "\r\n";
}

sub output {
    my ($r) = @_;
    if ($fmt eq 'text') {
        headers('text/plain; charset=utf-8');
        print $r->{total};
    } elsif ($fmt eq 'svg') {
        headers('image/svg+xml; charset=utf-8');
        print svg($r->{total});
    } else {
        headers('application/json; charset=utf-8');
        printf '{"page":"%s","total":%d,"today":%d,"yesterday":%d,"counted":%d}',
            $page, $r->{total}, $r->{today}, $r->{yesterday}, $r->{counted};
    }
}

sub svg {
    my $n = commify($_[0]);
    my $w = 16 + 9 * length($n);
    return qq{<svg xmlns="http://www.w3.org/2000/svg" width="$w" height="22" viewBox="0 0 $w 22">}
         . qq{<rect width="$w" height="22" rx="4" fill="#1d6b58"/>}
         . qq{<text x="${\($w/2)}" y="15.5" text-anchor="middle" font-family="monospace" font-size="14" fill="#fff">$n</text></svg>};
}

sub commify { my $n = reverse shift; $n =~ s/(\d{3})(?=\d)/$1,/g; return scalar reverse $n; }

sub parse_query {
    my %h;
    for my $pair (split /[&;]/, $_[0]) {
        my ($k, $v) = split /=/, $pair, 2;
        next unless defined $k;
        $v = '' unless defined $v;
        for ($k, $v) { tr/+/ /; s/%([0-9A-Fa-f]{2})/chr(hex($1))/ge; }
        $h{$k} = $v;
    }
    return %h;
}

sub fail {
    my ($status, $msg) = @_;
    my %text = (400 => 'Bad Request', 403 => 'Forbidden', 404 => 'Not Found', 500 => 'Internal Server Error');
    headers('application/json; charset=utf-8', "$status $text{$status}");
    print qq{{"error":"$msg"}};
    exit;
}

# =====================================================================
#  管理画面
# =====================================================================

sub admin_pass {
    open(my $fh, '<', "$DATA_DIR/$ADMIN_PASS_FILE") or return '';
    my $p = <$fh>;
    close $fh;
    return '' unless defined $p;
    $p =~ s/[\r\n]+\z//;
    $p =~ s/\A\s+|\s+\z//g;
    return $p;
}

sub admin_key { return sha256_hex($HASH_SALT . "\0" . $_[0]); }

sub same_str {   # 比較にかかる時間で中身が推測されないように全文字を比べる
    my ($a, $b) = @_;
    return 0 unless length($a) == length($b);
    my $r = 0;
    $r |= ord(substr($a, $_, 1)) ^ ord(substr($b, $_, 1)) for 0 .. length($a) - 1;
    return $r == 0;
}

sub cookies {
    my %c;
    for (split /;\s*/, $ENV{HTTP_COOKIE} || '') { my ($k, $v) = split /=/, $_, 2; $c{$k} = $v if defined $v; }
    return %c;
}

sub is_https { return (($ENV{HTTPS} || '') =~ /on|1/i) || (($ENV{HTTP_X_FORWARDED_PROTO} || '') eq 'https'); }

sub logged_in {
    my ($pass) = @_;
    my %c = cookies();
    my $tok = $c{ctr_admin} || '';
    my ($exp, $sig) = split /\./, $tok, 2;
    return 0 unless $exp && $sig && $exp =~ /\A\d+\z/ && $exp > time;
    return same_str($sig, hmac_sha256_hex("admin:$exp", admin_key($pass)));
}

sub html_headers {
    my ($extra) = @_;
    headers('text/html; charset=utf-8', undef,
        "X-Frame-Options: DENY\r\nReferrer-Policy: no-referrer\r\n" . ($extra || ''));
}

sub esc {
    my $s = defined $_[0] ? $_[0] : '';
    $s =~ s/&/&amp;/g; $s =~ s/</&lt;/g; $s =~ s/>/&gt;/g; $s =~ s/"/&quot;/g;
    return $s;
}

sub self_url { my $n = $ENV{SCRIPT_NAME} || 'counter.cgi'; $n =~ s{.*/}{}; return $n; }

sub admin_main {
    my $pass = admin_pass();
    my $me = self_url();

    if ($pass eq '') {
        html_headers();
        print page_html('管理画面は無効です', qq{<div class="card"><p>管理画面を使うには、データフォルダ（<code>${\ esc($DATA_DIR)}</code>）に <code>$ADMIN_PASS_FILE</code> を作り、1行目に管理用パスワードを書いてください。</p></div>});
        return;
    }

    if ($mode eq 'logout') {
        my $sec = is_https() ? '; Secure' : '';
        html_headers("Set-Cookie: ctr_admin=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict$sec\r\n");
        print page_html('ログアウトしました', qq{<div class="card"><p>ログアウトしました。</p><p><a href="$me?mode=admin">もう一度ログインする</a></p></div>});
        return;
    }

    if ($mode eq 'login') {
        my $len = $ENV{CONTENT_LENGTH} || 0;
        my $body = '';
        read(STDIN, $body, $len > 4096 ? 4096 : $len) if ($ENV{REQUEST_METHOD} || '') eq 'POST';
        my %f = parse_query($body);
        if (same_str($f{password} // '', $pass)) {
            my $exp = time + $ADMIN_HOURS * 3600;
            my $tok = $exp . '.' . hmac_sha256_hex("admin:$exp", admin_key($pass));
            my $sec = is_https() ? '; Secure' : '';
            print "Status: 303 See Other\r\nLocation: $me?mode=admin\r\nCache-Control: no-store\r\n"
                . "Set-Cookie: ctr_admin=$tok; Max-Age=" . ($ADMIN_HOURS * 3600) . "; Path=/; HttpOnly; SameSite=Strict$sec\r\n\r\n";
            return;
        }
        sleep 2;   # 総当たり対策
        html_headers();
        print login_html('パスワードが違います。');
        return;
    }

    unless (logged_in($pass)) { html_headers(); print login_html(''); return; }

    html_headers();
    my $tag = $q{page};
    if (valid_tag($tag) && -e "$DATA_DIR/$tag.dat") { print detail_html($tag); }
    else                                            { print overview_html(); }
}

sub login_html {
    my ($err) = @_;
    my $me = self_url();
    my $e = $err ? qq{<p class="err">} . esc($err) . '</p>' : '';
    return page_html('ログイン', qq{
<form class="card login" method="post" action="$me?mode=login">
  <label for="password">管理用パスワード</label>
  <input type="password" id="password" name="password" autocomplete="current-password" required autofocus>
  $e
  <button type="submit">ログイン</button>
</form>});
}

sub month_of { return substr($_[0], 0, 7); }
sub prev_month { my ($y, $m) = split /-/, $_[0]; $m--; if ($m < 1) { $m = 12; $y--; } return sprintf('%04d-%02d', $y, $m); }
sub next_month { my ($y, $m) = split /-/, $_[0]; $m++; if ($m > 12) { $m = 1; $y++; } return sprintf('%04d-%02d', $y, $m); }
sub days_in { my ($y, $m) = split /-/, $_[0]; my @d = (31,28,31,30,31,30,31,31,30,31,30,31); my $n = $d[$m - 1]; $n = 29 if $m == 2 && (($y % 4 == 0 && $y % 100) || $y % 400 == 0); return $n; }
sub wday { my ($y, $m, $d) = split /-/, $_[0]; ($m, $y) = ($m + 12, $y - 1) if $m < 3; return ($d + int((13 * ($m + 1)) / 5) + $y + int($y / 4) - int($y / 100) + int($y / 400) + 6) % 7; } # 0=日
sub fmt_md { my ($y, $m) = split /-/, $_[0]; return ($y + 0) . '年' . ($m + 0) . '月'; }

sub month_sums {
    my ($day) = @_;
    my %m;
    $m{ month_of($_) } += $day->{$_} for keys %$day;
    return \%m;
}

sub overview_html {
    my $me = self_url();
    my $now = time;
    my $this = month_of(ymd($now));
    my $last = prev_month($this);
    my @rows;
    my %sum;
    for my $tag (list_pages()) {
        my ($d, $day) = load_page($tag) or next;
        my $ms = month_sums($day);
        my @dates = sort keys %$day;
        my $r = { tag => $tag, total => $d->{total}, today => $d->{today}, yesterday => $d->{yesterday},
                  this => $ms->{$this} || 0, last => $ms->{$last} || 0, since => (@dates ? $dates[0] : '') };
        $sum{$_} += $r->{$_} for qw(total today yesterday this last);
        push @rows, $r;
    }
    @rows = sort { $b->{total} <=> $a->{total} || $a->{tag} cmp $b->{tag} } @rows;

    my $body = '';
    if (!@rows) {
        $body = '<div class="card"><p>まだ記録がありません。</p></div>';
    } else {
        my $tr = '';
        for my $r (@rows) {
            my $t = esc($r->{tag});
            $tr .= qq{<tr><th scope="row"><a href="$me?mode=admin&amp;page=$t">$t</a></th>}
                 . '<td>' . commify($r->{total}) . '</td><td>' . commify($r->{today}) . '</td><td>' . commify($r->{yesterday})
                 . '</td><td>' . commify($r->{this}) . '</td><td class="hide-sm">' . commify($r->{last}) . '</td><td class="since hide-sm">' . esc($r->{since}) . "</td></tr>\n";
        }
        my $foot = '';
        if (@rows > 1) {
            $foot = '<tfoot><tr><th scope="row">合計</th><td>' . join('</td><td>', map { commify($sum{$_}) } qw(total today yesterday this)) . '</td><td class="hide-sm">' . commify($sum{last}) . '</td><td class="hide-sm"></td></tr></tfoot>';
        }
        $body = qq{
<div class="kpis">
  <div><span>今日</span><b>${\ commify($sum{today})}</b></div>
  <div><span>昨日</span><b>${\ commify($sum{yesterday})}</b></div>
  <div><span>今月</span><b>${\ commify($sum{this})}</b></div>
  <div><span>合計</span><b>${\ commify($sum{total})}</b></div>
</div>
<div class="tablewrap card flush"><table class="num">
<thead><tr><th scope="col">ページ</th><th scope="col">合計</th><th scope="col">今日</th><th scope="col">昨日</th><th scope="col">今月</th><th scope="col" class="hide-sm">先月</th><th scope="col" class="hide-sm">記録開始</th></tr></thead>
<tbody>$tr</tbody>$foot
</table></div>
<p class="note">ページ名を押すと、月別・日別の記録を見られます。日別の記録はこの機能を入れた日から始まります（それ以前は合計数だけが残っています）。</p>};
    }
    return page_html('アクセス記録', $body, 1);
}

sub detail_html {
    my ($tag) = @_;
    my $me = self_url();
    my ($d, $day) = load_page($tag);
    my $today = ymd(time);
    my $ms = month_sums($day);
    my @dates = sort keys %$day;
    my $first = @dates ? $dates[0] : $today;
    my $firstm = month_of($first);
    my $cur = month_of($today);
    my $m = (defined $q{m} && $q{m} =~ /\A\d{4}-\d\d\z/) ? $q{m} : $cur;
    $m = $cur if $m gt $cur;
    $m = $firstm if $m lt $firstm;
    my $t = esc($tag);

    # 月別（記録開始月から今月まで）
    my @months;
    for (my $x = $cur; $x ge $firstm; $x = prev_month($x)) { push @months, $x; }
    my $mmax = 1; for (@months) { $mmax = $ms->{$_} if ($ms->{$_} || 0) > $mmax; }
    my $mrows = '';
    for my $x (@months) {
        my $n = $ms->{$x} || 0;
        my $days = ($x eq $cur) ? (split /-/, $today)[2] + 0 : days_in($x);
        my $from = ($x eq $firstm) ? (split /-/, $first)[2] + 0 : 1;
        my $span = $days - $from + 1; $span = 1 if $span < 1;
        my $avg = sprintf('%.1f', $n / $span);
        my $w = sprintf('%.1f', 100 * $n / $mmax);
        my $sel = $x eq $m ? ' class="sel"' : '';
        $mrows .= qq{<tr$sel><th scope="row"><a href="$me?mode=admin&amp;page=$t&amp;m=$x">${\ fmt_md($x)}</a></th>}
                . qq{<td>${\ commify($n)}</td><td>$avg</td><td class="barcell"><span class="bar" style="width:$w%"></span></td></tr>\n};
    }

    # 日別（選んだ月）
    my $nd = days_in($m);
    my $dmax = 1;
    for my $i (1 .. $nd) { my $k = sprintf('%s-%02d', $m, $i); $dmax = $day->{$k} if ($day->{$k} || 0) > $dmax; }
    my @wn = qw(日 月 火 水 木 金 土);
    my $drows = '';
    my $msum = 0;
    for my $i (1 .. $nd) {
        my $k = sprintf('%s-%02d', $m, $i);
        last if $k gt $today;
        my $wd = wday($k);
        my $cls = $wd == 0 ? 'sun' : $wd == 6 ? 'sat' : '';
        $cls .= ' today' if $k eq $today;
        my ($n, $w);
        if ($k lt $first) { $n = '<span class="na">–</span>'; $w = 0; }
        else { my $v = $day->{$k} || 0; $msum += $v; $n = commify($v); $w = sprintf('%.1f', 100 * $v / $dmax); }
        $drows .= qq{<tr class="$cls"><th scope="row">$i日<small>（$wn[$wd]）</small></th><td>$n</td><td class="barcell"><span class="bar" style="width:$w%"></span></td></tr>\n};
    }
    my $prev = prev_month($m);
    my $next = next_month($m);
    my $pl = $prev ge $firstm ? qq{<a href="$me?mode=admin&amp;page=$t&amp;m=$prev">← ${\ fmt_md($prev)}</a>} : '<span></span>';
    my $nl = $next le $cur    ? qq{<a href="$me?mode=admin&amp;page=$t&amp;m=$next">${\ fmt_md($next)} →</a>} : '<span></span>';

    my $body = qq{
<p class="crumb"><a href="$me?mode=admin">← すべてのページ</a></p>
<div class="kpis">
  <div><span>今日</span><b>${\ commify($d->{today})}</b></div>
  <div><span>昨日</span><b>${\ commify($d->{yesterday})}</b></div>
  <div><span>今月</span><b>${\ commify($ms->{$cur} || 0)}</b></div>
  <div><span>合計</span><b>${\ commify($d->{total})}</b></div>
</div>
<div class="cols">
<section>
  <h3>月別</h3>
  <div class="card flush"><table class="num bars">
  <thead><tr><th scope="col">月</th><th scope="col">件数</th><th scope="col">日平均</th><th scope="col" class="barcell"><span class="sr">グラフ</span></th></tr></thead>
  <tbody>$mrows</tbody></table></div>
</section>
<section>
  <h3>日別 ・ ${\ fmt_md($m)}（${\ commify($msum)}件）</h3>
  <nav class="monthnav">$pl$nl</nav>
  <div class="card flush"><table class="num bars">
  <thead><tr><th scope="col">日</th><th scope="col">件数</th><th scope="col" class="barcell"><span class="sr">グラフ</span></th></tr></thead>
  <tbody>$drows</tbody></table></div>
</section>
</div>
<p class="note">記録開始: ${\ esc($first)}　同じ人が1時間以内に再訪した分は数えていません。</p>};
    return page_html("$tag のアクセス記録", $body, 1);
}

sub page_html {
    my ($title, $body, $logout) = @_;
    my $me = self_url();
    my $out = $logout ? qq{<a class="logout" href="$me?mode=logout">ログアウト</a>} : '';
    my $t = esc($title);
    return <<"HTML";
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>$t ・ カウンター管理</title>
<style>
:root {
  --bg: #f1f4f2; --surface: #ffffff; --fg: #1b2420; --muted: #5d6a64; --line: #d6ded9;
  --accent: #1d6b58; --accent-soft: #dcebe5; --bar: #3f8f78; --sun: #b0452a; --sat: #2b5ea7; --warn: #9c4419;
  color-scheme: light;
}
\@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111715; --surface: #19211e; --fg: #e1e8e4; --muted: #94a39c; --line: #2b3632;
    --accent: #5cbca0; --accent-soft: #1d2f29; --bar: #4fae92; --sun: #ec8a6f; --sat: #7fa8e8; --warn: #e3905f;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.6 "Hiragino Sans", "Noto Sans JP", "Yu Gothic UI", Meiryo, system-ui, sans-serif; }
.wrap { max-width: 1040px; margin: 0 auto; padding-block: 20px 48px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: flex; flex-direction: column; gap: 16px; }
header { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
h1 { margin: 0; font-size: 19px; }
h1 small { font-size: 12px; color: var(--muted); font-weight: 600; letter-spacing: .08em; display: block; }
h2.tag { margin: 0; font: 700 20px ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; }
h3 { margin: 0 0 8px; font-size: 14px; color: var(--muted); letter-spacing: .04em; }
a { color: var(--accent); }
.logout { font-size: 13px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px; }
.card.flush { padding: 0; overflow-x: auto; }
.card p { margin: 0 0 8px; } .card p:last-child { margin: 0; }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.kpis div { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; }
.kpis span { font-size: 12px; color: var(--muted); }
.kpis b { font-size: 22px; font-variant-numeric: tabular-nums; }
\@media (max-width: 460px) { .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 7px 12px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
thead th { font-size: 12px; color: var(--muted); font-weight: 600; background: var(--bg); }
th[scope=row], thead th:first-child { text-align: left; }
tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
tfoot th, tfoot td { font-weight: 700; border-top: 2px solid var(--line); border-bottom: 0; }
td.since { color: var(--muted); font-size: 13px; }
\@media (max-width: 560px) {
  th, td { padding: 7px 8px; }
  .hide-sm { display: none; }
}
.barcell { width: 45%; min-width: 80px; text-align: left; }
.bar { display: block; height: 10px; border-radius: 2px; background: var(--bar); min-width: 0; }
tr.sel { background: var(--accent-soft); }
tr.sun th { color: var(--sun); } tr.sat th { color: var(--sat); }
tr.today { background: var(--accent-soft); }
th small { color: var(--muted); font-weight: 400; margin-left: 2px; }
.na { color: var(--muted); }
.cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }
\@media (max-width: 760px) { .cols { grid-template-columns: minmax(0, 1fr); } }
.monthnav { display: flex; justify-content: space-between; margin: -2px 0 8px; font-size: 13.5px; }
.crumb { margin: 0; font-size: 13.5px; }
.note { margin: 0; font-size: 12.5px; color: var(--muted); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.login { max-width: 360px; display: flex; flex-direction: column; gap: 8px; }
.login label { font-size: 13px; color: var(--muted); }
.login input { font: inherit; font-size: 16px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--fg); }
.login button { font: inherit; font-weight: 700; padding: 10px; border: 0; border-radius: 8px; background: var(--accent); color: var(--surface); cursor: pointer; }
.err { color: var(--warn); margin: 0; font-size: 13.5px; }
code { font-size: .9em; background: var(--accent-soft); padding: 1px 5px; border-radius: 4px; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
</head>
<body>
<div class="wrap">
<header><h1><small>COUNTER</small>$t</h1>$out</header>
$body
</div>
</body>
</html>
HTML
}
