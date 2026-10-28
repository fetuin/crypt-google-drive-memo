#!/usr/bin/perl
#
# counter.cgi  汎用アクセスカウンター（複数ページ対応）
#
# ■ 呼び出し方（page= にページごとのタグを付ける）
#   counter.cgi?page=kagimemo            … 1回数えて、結果をJSONで返す
#   counter.cgi?page=kagimemo&mode=view  … 数えずに現在の値だけ返す
#   counter.cgi?page=kagimemo&fmt=text   … 合計数だけをテキストで返す
#   counter.cgi?page=kagimemo&fmt=svg    … 画像（SVG）で返す。<img src="..."> でJavaScriptなしでも使える
#
#   JSONの例: {"page":"kagimemo","total":1234,"today":12,"yesterday":34,"counted":1}
#
# ■ データ
#   ページごとに $DATA_DIR/<タグ>.dat に保存します（1ページ1ファイル）。
#   タグに使える文字は 英数字・ハイフン・アンダースコア（64文字まで）。
#   同じ人（IPアドレス）が $REPEAT_SEC 秒以内に再訪しても数えません。
#   IPアドレスはそのまま保存せず、ハッシュ値（一部）だけを保存します。
#
# ■ 設置
#   1. このファイルと counter_data フォルダ（.htaccess 入り）をサーバーにアップロード
#   2. counter.cgi のパーミッションを 755（サーバーの指定があればそれに従う）
#   3. counter_data フォルダを 755（書き込めない場合は 707 か 777）
#   4. 1行目のPerlのパスがサーバーと違う場合は書き換える（例: #!/usr/local/bin/perl）
#
use strict;
use warnings;
use Fcntl qw(:flock O_RDWR O_CREAT);
use Digest::MD5 qw(md5_hex);

# ===== 設定 =====
my $DATA_DIR    = './counter_data';   # データの保存先（このCGIからの相対パス）
my %ALLOW       = ();                 # 使うページを限定する場合: ( 'kagimemo' => 1, 'top' => 1 )
my $MAX_PAGES   = 100;                # %ALLOW が空のとき、自動で作れるページ数の上限（いたずら対策）
my $REPEAT_SEC  = 3600;               # 同じIPからの再訪を数えない秒数（0で毎回数える）
my $IP_KEEP     = 300;                # 再訪判定のために覚えておくIPの件数（ページごと）
my $HASH_SALT   = 'change-this-salt'; # IPハッシュ用の文字列（設置時に任意の文字列へ変更推奨）
my $TZ_OFFSET   = 9 * 3600;           # 日付の区切り（日本時間）
my @CORS_ORIGIN = ('*');              # ほかのドメインのページから呼ぶ場合の許可。限定するなら ('https://example.com')
# ===============

my %q = parse_query($ENV{QUERY_STRING} || '');
my $page = defined $q{page} ? $q{page} : '';
my $fmt  = defined $q{fmt}  ? $q{fmt}  : 'json';
my $mode = defined $q{mode} ? $q{mode} : 'count';

if ($page !~ /\A[A-Za-z0-9_-]{1,64}\z/) { fail(400, 'invalid page tag'); }
if (%ALLOW && !$ALLOW{$page})          { fail(404, 'unknown page tag'); }

unless (-d $DATA_DIR) { mkdir $DATA_DIR, 0755 or fail(500, 'cannot create data dir'); }
my $file = "$DATA_DIR/$page.dat";

if (!-e $file && !%ALLOW) {
    opendir(my $dh, $DATA_DIR) or fail(500, 'cannot read data dir');
    my $n = grep { /\.dat\z/ } readdir $dh;
    closedir $dh;
    fail(403, 'too many pages') if $n >= $MAX_PAGES;
}
if (!-e $file && $mode eq 'view') {
    output({ total => 0, today => 0, yesterday => 0, counted => 0 });
    exit;
}

# ---- 読み込み・更新（ファイルロック付き） ----
sysopen(my $fh, $file, O_RDWR | O_CREAT, 0644) or fail(500, 'cannot open data file');
flock($fh, LOCK_EX) or fail(500, 'cannot lock data file');
binmode $fh;

my %d = (total => 0, date => '', today => 0, yesterday => 0);
my @ips;
while (my $line = <$fh>) {
    $line =~ s/[\r\n]+\z//;
    my ($k, $v) = split /=/, $line, 2;
    next unless defined $v;
    if ($k eq 'ip') { push @ips, $v; } else { $d{$k} = $v; }
}
$d{$_} = int($d{$_} || 0) for qw(total today yesterday);

my $now   = time;
my $today = ymd($now);
if ($d{date} ne $today) {
    $d{yesterday} = ($d{date} eq ymd($now - 86400)) ? $d{today} : 0;
    $d{today} = 0;
    $d{date}  = $today;
}

my $counted = 0;
if ($mode ne 'view') {
    my $ip = substr(md5_hex($HASH_SALT . ($ENV{REMOTE_ADDR} || '')), 0, 12);
    my $seen = 0;
    my @keep;
    if ($REPEAT_SEC > 0) {
        for my $e (@ips) {
            my ($h, $t) = split /:/, $e, 2;
            next unless $h && defined $t && $t =~ /\A\d+\z/;
            next if $now - $t >= $REPEAT_SEC;        # 期限切れは捨てる
            $seen = 1 if $h eq $ip;                  # 最初に数えた時刻のまま残す（期限は延ばさない）
            push @keep, "$h:$t";
        }
        push @keep, "$ip:$now" unless $seen;
        @keep = @keep[-$IP_KEEP .. -1] if @keep > $IP_KEEP;
    }
    unless ($seen) { $d{total}++; $d{today}++; $counted = 1; }
    @ips = @keep;

    seek($fh, 0, 0);
    truncate($fh, 0);
    print {$fh} "total=$d{total}\n", "date=$d{date}\n", "today=$d{today}\n", "yesterday=$d{yesterday}\n";
    print {$fh} "ip=$_\n" for @ips;
}
close $fh;   # ロックも解除される

output({ total => $d{total}, today => $d{today}, yesterday => $d{yesterday}, counted => $counted });
exit;

# ---------- 以下、関数 ----------

sub ymd {
    my @t = gmtime($_[0] + $TZ_OFFSET);
    return sprintf('%04d-%02d-%02d', $t[5] + 1900, $t[4] + 1, $t[3]);
}

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

sub headers {
    my ($type, $status) = @_;
    my $h = '';
    $h .= "Status: $status\r\n" if $status;
    $h .= "Content-Type: $type\r\n";
    $h .= "Cache-Control: no-store, max-age=0\r\n";
    $h .= "X-Content-Type-Options: nosniff\r\n";
    if (@CORS_ORIGIN) {
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

sub fail {
    my ($status, $msg) = @_;
    my %text = (400 => 'Bad Request', 403 => 'Forbidden', 404 => 'Not Found', 500 => 'Internal Server Error');
    headers('application/json; charset=utf-8', "$status $text{$status}");
    print qq{{"error":"$msg"}};
    exit;
}
