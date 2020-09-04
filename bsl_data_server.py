#!/usr/bin/env python2
# coding: utf-8

import os,socket,threading,time
import sys
import serial
import codecs
import signal
from subprocess import call
import ConfigParser

allow_delete = False
local_ip = 'localhost'
local_port = 10001
currdir=os.path.abspath('.')

# Global state event
# 'Clear' means serial in use by tos-bsl, 'Set' release it to data channel use
controlEvent = threading.Event()
controlEventAnswer = threading.Event()

# Gobal serial retry control
serial_dev_err = 0

class FTPserverThread(threading.Thread):
    def __init__(self,(conn,addr)):
        if not options.quiet:
            sys.stderr.write('FTP Connected by %s\n' % (addr,))
        self.conn=conn
        self.addr=addr
        self.basewd=currdir
        self.cwd=self.basewd
        self.rest=False
        self.pasv_mode=False
        threading.Thread.__init__(self)

    def run(self):
        self.conn.send('220 Welcome!\r\n')
        while True:
            cmd=self.conn.recv(256)
            if not cmd: break
            else:
                if not options.quiet: print 'Received:',cmd
                try:
                    func=getattr(self,cmd[:4].strip().upper())
                    func(cmd)
                except Exception,e:
                    print 'ERROR:',e
                    self.conn.send('500 Sorry.\r\n')

    def SYST(self,cmd):
        self.conn.send('215 UNIX Type: L8\r\n')
    def OPTS(self,cmd):
        if cmd[5:-2].upper()=='UTF8 ON':
            self.conn.send('200 OK.\r\n')
        else:
            self.conn.send('451 Sorry.\r\n')
    def USER(self,cmd):
        self.conn.send('331 OK.\r\n')
    def PASS(self,cmd):
        self.conn.send('230 OK.\r\n')
        #self.conn.send('530 Incorrect.\r\n')
    def QUIT(self,cmd):
        self.conn.send('221 Goodbye.\r\n')
    def NOOP(self,cmd):
        self.conn.send('200 OK.\r\n')
    def TYPE(self,cmd):
        self.mode=cmd[5]
        self.conn.send('200 Binary mode.\r\n')

    def CDUP(self,cmd):
        if not os.path.samefile(self.cwd,self.basewd):
            #learn from stackoverflow
            self.cwd=os.path.abspath(os.path.join(self.cwd,'..'))
        self.conn.send('200 OK.\r\n')
    def PWD(self,cmd):
        cwd=os.path.relpath(self.cwd,self.basewd)
        if cwd=='.':
            cwd='/'
        else:
            cwd='/'+cwd
        self.conn.send('257 \"%s\"\r\n' % cwd)
    def CWD(self,cmd):
        chwd=cmd[4:-2]
        if chwd=='/':
            self.cwd=self.basewd
        elif chwd[0]=='/':
            self.cwd=os.path.join(self.basewd,chwd[1:])
        else:
            self.cwd=os.path.join(self.cwd,chwd)
        self.conn.send('250 OK.\r\n')

    def PORT(self,cmd):
        if self.pasv_mode:
            self.servsock.close()
            self.pasv_mode = False
        l=cmd[5:].split(',')
        self.dataAddr='.'.join(l[:4])
        self.dataPort=(int(l[4])<<8)+int(l[5])
        self.conn.send('200 Get port.\r\n')

    def PASV(self,cmd): # from http://goo.gl/3if2U
        self.pasv_mode = True
        self.servsock = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.servsock.bind((local_ip,0))
        self.servsock.listen(1)
        ip, port = self.servsock.getsockname()
        if not options.quiet: print 'open', ip, port
        self.conn.send('227 Entering Passive Mode (%s,%u,%u).\r\n' %
                (','.join(ip.split('.')), port>>8&0xFF, port&0xFF))

    def start_datasock(self):
        if not options.quiet: print 'start_datasock(): PASV=', self.pasv_mode
        if self.pasv_mode:
            self.datasock, addr = self.servsock.accept()
            if not options.quiet: print 'connect:', addr
        else:
            self.datasock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            self.datasock.connect((self.dataAddr,self.dataPort))

    def stop_datasock(self):
        self.datasock.close()
        if self.pasv_mode:
            self.servsock.close()


    def LIST(self,cmd):
        self.conn.send('150 Here comes the directory listing.\r\n')
        if not options.quiet: print 'list1:', self.cwd
        self.start_datasock()
        if not options.quiet: print 'list2:', self.cwd
        print self.cwd
        for t in os.listdir(self.cwd):
            k=self.toListItem(os.path.join(self.cwd,t))
            self.datasock.send(k+'\r\n')
        self.stop_datasock()
        self.conn.send('226 Directory send OK.\r\n')

    def toListItem(self,fn):
        st=os.stat(fn)
        fullmode='rwxrwxrwx'
        mode=''
        for i in range(9):
            mode+=((st.st_mode>>(8-i))&1) and fullmode[i] or '-'
        d=(os.path.isdir(fn)) and 'd' or '-'
        ftime=time.strftime(' %b %d %H:%M ', time.gmtime(st.st_mtime))
        return d+mode+' 1 user group '+str(st.st_size)+ftime+os.path.basename(fn)

    def MKD(self,cmd):
        dn=os.path.join(self.cwd,cmd[4:-2])
        os.mkdir(dn)
        self.conn.send('257 Directory created.\r\n')

    def RMD(self,cmd):
        dn=os.path.join(self.cwd,cmd[4:-2])
        if allow_delete:
            os.rmdir(dn)
            self.conn.send('250 Directory deleted.\r\n')
        else:
            self.conn.send('450 Not allowed.\r\n')

    def DELE(self,cmd):
        fn=os.path.join(self.cwd,cmd[5:-2])
        if allow_delete:
            os.remove(fn)
            self.conn.send('250 File deleted.\r\n')
        else:
            self.conn.send('450 Not allowed.\r\n')

    def RNFR(self,cmd):
        self.rnfn=os.path.join(self.cwd,cmd[5:-2])
        self.conn.send('350 Ready.\r\n')

    def RNTO(self,cmd):
        fn=os.path.join(self.cwd,cmd[5:-2])
        os.rename(self.rnfn,fn)
        self.conn.send('250 File renamed.\r\n')

    def REST(self,cmd):
        self.pos=int(cmd[5:-2])
        self.rest=True
        self.conn.send('250 File position reseted.\r\n')

    def RETR(self,cmd):
        fn=os.path.join(self.cwd,cmd[5:-2])
        #fn=os.path.join(self.cwd,cmd[5:-2]).lstrip('/')
        if not options.quiet: print 'Downlowding:',fn
        if self.mode=='I':
            fi=open(fn,'rb')
        else:
            fi=open(fn,'r')
        self.conn.send('150 Opening data connection.\r\n')
        if self.rest:
            fi.seek(self.pos)
            self.rest=False
        data= fi.read(1024)
        self.start_datasock()
        while data:
            self.datasock.send(data)
            data=fi.read(1024)
        fi.close()
        self.stop_datasock()
        self.conn.send('226 Transfer complete.\r\n')

    def STOR(self,cmd):
        # Reserve Serial Control and wait it back
        controlEvent.clear()
        if controlEventAnswer.isSet() == False:
            controlEventAnswer.wait()
        time.sleep(0.1) # give some time to release port
        if not options.quiet: sys.stderr.write('FTP controlEvent: %s\n' % controlEvent.isSet())
        file_name = cmd[5:-2]
        fn=os.path.join(self.cwd,cmd[5:-2])
        if not options.quiet: print 'Uploading:',fn
        if self.mode=='I':
            fo=open(fn,'wb')
        else:
            fo=open(fn,'w')
        self.conn.send('150 Opening data connection.\r\n')
        self.start_datasock()
        while True:
            data=self.datasock.recv(1024)
            if not data: break
            fo.write(data)
        fo.close()
        self.stop_datasock()
	# call tos-bsl
	sh_cmd = 'python /home/pi/TBControl/files/tos-bsl.py --telosb -c '+serial_dev+' -r -e -I -p '+file_name
	if not options.quiet: sys.stderr.write('cmd: %s\n' % sh_cmd)
        bsl_stat = call(sh_cmd,shell=True)
        retry = 0
        while bsl_stat != 0 and retry < 3 :
            time.sleep(0.2) # give some time to retry
            bsl_stat = call(sh_cmd,shell=True) # retry
            retry = retry + 1
        if bsl_stat == 0:
            self.conn.send('226 Transfer complete.\r\n')
        else:
            self.conn.send('550 Requested action not taken.\r\n')
        # Release Serial control
        time.sleep(0.1) # give time to bsl release it
        controlEvent.set()
        if not options.quiet:
            sys.stderr.write('FTP controlEvent: %s\n' % controlEvent.isSet())

    def AUTH(self,cmd):
       self.conn.send('200 Command okay.\r\n')
    def SIZE(self,cmd):
       self.conn.send('200 Command okay.\r\n')

class Redirector:
    def __init__(self, serial_instance, socket, ser_newline=None, net_newline=None, spy=False):
        self.serial = serial_instance
        self.socket = socket
        self.ser_newline = ser_newline
        self.net_newline = net_newline
        self.spy = spy
        self._write_lock = threading.Lock()

    def shortcut(self):
        """connect the serial port to the TCP port by copying everything
           from one side to the other"""
        if not options.quiet: sys.stderr.write(">> shortcut enter\n")
        self.alive = True
        self.thread_read = threading.Thread(target=self.reader)
        self.thread_read.setDaemon(True)
        self.thread_read.setName('serial->socket')
        self.thread_read.start()
        self.socket.settimeout(1)
        self.writer()
        if not options.quiet: sys.stderr.write(">> shortcut exit\n")

    def reader(self):
        """loop forever and copy serial->socket"""
        while self.alive:
            try:
                if controlEvent.isSet() == False:
                    break
                data = self.serial.read(1)              # read one, blocking
                n = self.serial.inWaiting()             # look if there is more
                if n:
                    data = data + self.serial.read(n)   # and get as much as possible
                if data:
                    # the spy shows what's on the serial port, so log it before converting newlines
                    if self.spy:
                        sys.stdout.write(codecs.escape_encode(data)[0])
                        sys.stdout.flush()
                    #if self.ser_newline and self.net_newline:
                        # do the newline conversion
                        # XXX fails for CR+LF in input when it is cut in half at the begin or end of the string
                        #data = net_newline.join(data.split(ser_newline))
                    # escape outgoing data when needed (Telnet IAC (0xff) character)
                    self._write_lock.acquire()
                    try:
                        # Only send data to socket if it is in active state
                        if controlEvent.isSet() == True:
                            self.socket.sendall(data)           # send it over TCP
                    except Exception, msg:
                        sys.stderr.write('reader Socket ERROR IOError: %s\n' % msg)
                    finally:
                        self._write_lock.release()
            except IOError, msg:
                sys.stderr.write('reader ERROR IOError: %s\n' % msg)
                break
            except socket.error, msg:
                sys.stderr.write('reader ERROR socket.error: %s\n' % msg)
                break
            except Exception, msg:
                sys.stderr.write('reader ERROR Other Exception: %s\n' % msg)
                break

    def write(self, data):
        """thread safe socket write with no data escaping. used to send telnet stuff"""
        self._write_lock.acquire()
        try:
            self.socket.sendall(data)
        finally:
            self._write_lock.release()

    def writer(self):
        """loop forever and copy socket->serial"""
        while self.alive:
            try:
                if controlEvent.isSet() == False:
                    self.alive = False
                    self.thread_read.join()
                    break
                data = self.socket.recv(1024)
                if not data:
                    break
                #if self.ser_newline and self.net_newline:
                    # do the newline conversion
                    # XXX fails for CR+LF in input when it is cut in half at the begin or end of the string
                    #data = ser_newline.join(data.split(net_newline))
                # Only send data to serial if it is in active state
                if controlEvent.isSet() == True:
                    self.serial.write(data)                 # get a bunch of bytes and send them
                # the spy shows what's on the serial port, so log it after converting newlines
                if self.spy:
                    sys.stdout.write(codecs.escape_encode(data)[0])
                    sys.stdout.flush()
            except socket.timeout:
                continue
            except socket.error, msg:
                sys.stderr.write('writer socket.error: %s\n' % msg)
                # probably got disconnected
                break
            except IOError, msg:
                sys.stderr.write('writer IOError: %s\n' % msg)
            except Exception, msg:
                sys.stderr.write('writer Other Exception: %s\n' % msg)
        #self.alive = False

    def stop(self):
        """Stop copying"""
        if self.alive:
            self.alive = False
            self.thread_read.join()



class DATAserver(threading.Thread):
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(('',local_port + 1))
        except socket.error, e:
            sys.stderr.write('DC ERROR (bind port:%s): %s\n' % (local_port+1,e))
            exit(1)
        threading.Thread.__init__(self)            

    def run(self):
        global serial_dev_err
        self.sock.listen(1)
        while True:
            try:
                if not options.quiet:
                    sys.stderr.write("DC  Waiting for connection on %s...\n" % (local_port+1))
                connection, addr = self.sock.accept()
                if not options.quiet:
                    sys.stderr.write('DC  Connected by %s\n' % (addr,))
                while True:
#                  try:
		        # if necessary, waits for serial be released by control.
		        if controlEvent.isSet() == False:
                            if not options.quiet: sys.stderr.write("DC waiting for controlEvent\n") 
		            controlEvent.wait()
                        if not options.quiet: sys.stderr.write("DC Connecting to Serial Port %s\n" %serial_dev)
		        # connect to serial port
                        ser = serial.Serial()
		        ser.port     = serial_dev
		        ser.baudrate = serial_baudrate
		        ser.timeout  = 1     # required so that the reader thread can exit
		        try:
		            ser.open()
		        except Exception, e:
		            sys.stderr.write("DC Could not open serial port %s: %s\n" % (ser.portstr, e))
                            #break
                        serial_dev_err = 0
		        # enter 'network <-> serial' loop
                        r = Redirector(
                            ser,
                            connection,
                            spy=options.spy
                        )
		        try:
                            r.shortcut()
		        except Exception, e:
		            sys.stderr.write("r.shortcut() exit [%s]\n" % e)
                        try:
                            ser.flushInput()
                            ser.flushOutput()
                            ser.close()
		        except Exception, e:
		            sys.stderr.write("ser.xxx() ERROR [%s]\n" % e)
                        # Release ftp-bsl to work
                        controlEventAnswer.set()
                        if not options.quiet: sys.stderr.write('DC Serial Disconnected\n')
                        if controlEvent.isSet() == True:
                            break
                        
##                  except Exception, e:
##                      serial_dev_err = serial_dev_err + 1
##                      try:
##                          ser.close()
##                      except Exception,e:
##		            sys.stderr.write("Retry ser.close() [%s]\n" % e)
##                      if serial_dev_err > 10:
##                          sys.stderr.write('DC Run Serial fail %s [%s]\n' %(serial_dev_err,e))
##                          serial_dev_err = 0
##                          break
##                      else:
##                          sys.stderr.write('DC Run Serial retry %s [%s]\n' %(serial_dev_err,e))
##                          time.sleep(1)
##                          continue
                connection.close()
                if not options.quiet:
                    sys.stderr.write('DC TCP Connection closed\n')

            except socket.error, msg:
                sys.stderr.write('DC ERROR: %s\n' % msg)
              

    def stop(self):
        if not options.quiet:
            sys.stderr.write("DC sock.close()\n")
        self.sock.shutdown(socket.SHUT_RDWR)
        self.sock.close()

class FTPserver(threading.Thread):
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(('',local_port))
        except socket.error, e:
            sys.stderr.write('FTP ERROR (bind port:%s): %s\n' % (local_port,e))
            exit(1)
        threading.Thread.__init__(self)

    def run(self):
        self.sock.listen(2)
        while True:
            if not options.quiet:
                sys.stderr.write("FTP Waiting for connection on %s...\n" % (local_port))
            th=FTPserverThread(self.sock.accept())
            th.daemon=True
            th.start()

    def stop(self):
        self.sock.shutdown(socket.SHUT_RDWR)
        self.sock.close()

def signal_handler(signal, frame):
    if not options.quiet:
        sys.stderr.write("\rProgram stopped!\n")
    ftp.stop()
    dchannel.stop()
    sys.exit(0)

if __name__=='__main__':
    import optparse

    parser = optparse.OptionParser(
        usage = "%prog [options]",
        description = "FTP-bsl + TCP-data-relay service.",
        epilog = """\
NOTE: no security measures are implemented. Anyone can remotely connect
to this service over the network.

Only one connection at once is supported. When the connection is terminated
it waits for the next connect.

It is based on 'ftpserver.py' from https://gist.github.com/scturtle
and 'tcp_serial_redirect.py' from http://pyserial.sourceforge.net/.
Also it uses 'tos-bsl' program from http://tinyos.net/ repository.
""")


    parser.add_option("-m", "--mote",
        dest = "mote_sn",
        type = "string",
        action = "store",
        help = "mote serial number. ex: SX4D53F1",
        default = 'SX4D53F1'
    )

    parser.add_option("-q", "--quiet",
        dest = "quiet",
        action = "store_true",
        help = "suppress non error messages",
        default = False
    )

    parser.add_option("--spy",
        dest = "spy",
        action = "store_true",
        help = "peek at the communication and print all data to the console",
        default = False
    )


    (options, args) = parser.parse_args()
    # Force quiet=False
    options.quiet = False


    cp = ConfigParser.ConfigParser()
    cp.read("tb_motes.cfg")
    try:
        local_port,serial_com,serial_dev,serial_baudrate = cp.get('common',options.mote_sn).split(',')
    except ConfigParser.NoOptionError, e:
        sys.stderr.write("Invalid Mote Serial Number: %s \n" % options.mote_sn)
        exit(1)
    local_port = int(local_port)
    serial_baudrate = int(serial_baudrate)
    if not options.quiet:
        sys.stderr.write("--- FTP-bsl + Serial relay --- type Ctrl-C / BREAK to quit\n")
        sys.stderr.write("--- %s %s %s %s ---\n" % (local_port,serial_com,serial_dev, serial_baudrate))
    # Set 'controlEvent' to release serial port at statup
    controlEvent.set()
    controlEventAnswer.set()
    ftp=FTPserver()
    ftp.daemon=True
    ftp.start()
    dchannel=DATAserver()
    dchannel.daemon=True
    dchannel.start()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGQUIT, signal_handler)
    signal.pause()


