# -*- coding: utf-8 -*-
#
# gPodder - A media aggregator and podcast client
# Copyright (c) 2005-2011 Thomas Perl and the gPodder Team
#
# gPodder is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# gPodder is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#


#
#  download.py -- Download queue management
#  Thomas Perl <thp@perli.net>   2007-09-15
#
#  Based on libwget.py (2005-10-29)
#

from __future__ import with_statement

from gpodder.liblogger import log
from gpodder import util
from gpodder import youtube
import gpodder

import threading
import urllib
import urlparse
import shutil
import os.path
import os
import time
import collections

import mimetypes
import email
import email.Header

from xml.sax import saxutils

_ = gpodder.gettext

def get_header_param(headers, param, header_name):
    """Extract a HTTP header parameter from a dict

    Uses the "email" module to retrieve parameters
    from HTTP headers. This can be used to get the
    "filename" parameter of the "content-disposition"
    header for downloads to pick a good filename.

    Returns None if the filename cannot be retrieved.
    """
    try:
        headers_string = ['%s:%s'%(k,v) for k,v in headers.items()]
        msg = email.message_from_string('\n'.join(headers_string))
        if header_name in msg:
            value = msg.get_param(param, header=header_name)
            if value is None:
                return None
            decoded_list = email.Header.decode_header(value)
            value = []
            for part, encoding in decoded_list:
                if encoding:
                    value.append(part.decode(encoding))
                else:
                    value.append(unicode(part))
            return u''.join(value)
    except Exception, e:
        log('Error trying to get %s from %s: %s', \
                param, header_name, str(e), traceback=True)

    return None

class ContentRange(object):
    # Based on:
    # http://svn.pythonpaste.org/Paste/WebOb/trunk/webob/byterange.py
    #
    # Copyright (c) 2007 Ian Bicking and Contributors
    #
    # Permission is hereby granted, free of charge, to any person obtaining
    # a copy of this software and associated documentation files (the
    # "Software"), to deal in the Software without restriction, including
    # without limitation the rights to use, copy, modify, merge, publish,
    # distribute, sublicense, and/or sell copies of the Software, and to
    # permit persons to whom the Software is furnished to do so, subject to
    # the following conditions:
    #
    # The above copyright notice and this permission notice shall be
    # included in all copies or substantial portions of the Software.
    #
    # THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
    # EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    # MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
    # NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
    # LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
    # OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
    # WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
    """
    Represents the Content-Range header

    This header is ``start-stop/length``, where stop and length can be
    ``*`` (represented as None in the attributes).
    """

    def __init__(self, start, stop, length):
        assert start >= 0, "Bad start: %r" % start
        assert stop is None or (stop >= 0 and stop >= start), (
            "Bad stop: %r" % stop)
        self.start = start
        self.stop = stop
        self.length = length

    def __repr__(self):
        return '<%s %s>' % (
            self.__class__.__name__,
            self)

    def __str__(self):
        if self.stop is None:
            stop = '*'
        else:
            stop = self.stop + 1
        if self.length is None:
            length = '*'
        else:
            length = self.length
        return 'bytes %s-%s/%s' % (self.start, stop, length)

    def __iter__(self):
        """
        Mostly so you can unpack this, like:

            start, stop, length = res.content_range
        """
        return iter([self.start, self.stop, self.length])

    @classmethod
    def parse(cls, value):
        """
        Parse the header.  May return None if it cannot parse.
        """
        if value is None:
            return None
        value = value.strip()
        if not value.startswith('bytes '):
            # Unparseable
            return None
        value = value[len('bytes '):].strip()
        if '/' not in value:
            # Invalid, no length given
            return None
        range, length = value.split('/', 1)
        if '-' not in range:
            # Invalid, no range
            return None
        start, end = range.split('-', 1)
        try:
            start = int(start)
            if end == '*':
                end = None
            else:
                end = int(end)
            if length == '*':
                length = None
            else:
                length = int(length)
        except ValueError:
            # Parse problem
            return None
        if end is None:
            return cls(start, None, length)
        else:
            return cls(start, end-1, length)


class DownloadCancelledException(Exception): pass
class AuthenticationError(Exception): pass

class gPodderDownloadHTTPError(Exception):
    def __init__(self, url, error_code, error_message):
        self.url = url
        self.error_code = error_code
        self.error_message = error_message

class DownloadURLOpener(urllib.FancyURLopener):
    version = gpodder.user_agent

    # Sometimes URLs are not escaped correctly - try to fix them
    # (see RFC2396; Section 2.4.3. Excluded US-ASCII Characters)
    # FYI: The omission of "%" in the list is to avoid double escaping!
    ESCAPE_CHARS = dict((ord(c), u'%%%x'%ord(c)) for c in u' <>#"{}|\\^[]`')

    def __init__( self, channel):
        self.channel = channel
        self._auth_retry_counter = 0
        urllib.FancyURLopener.__init__(self, None)

    def http_error_default(self, url, fp, errcode, errmsg, headers):
        """
        FancyURLopener by default does not raise an exception when
        there is some unknown HTTP error code. We want to override
        this and provide a function to log the error and raise an
        exception, so we don't download the HTTP error page here.
        """
        # The following two lines are copied from urllib.URLopener's
        # implementation of http_error_default
        void = fp.read()
        fp.close()
        raise gPodderDownloadHTTPError(url, errcode, errmsg)
    
    def redirect_internal(self, url, fp, errcode, errmsg, headers, data):
        """ This is the exact same function that's included with urllib
            except with "void = fp.read()" commented out. """
        
        if 'location' in headers:
            newurl = headers['location']
        elif 'uri' in headers:
            newurl = headers['uri']
        else:
            return
        
        # This blocks forever(?) with certain servers (see bug #465)
        #void = fp.read()
        fp.close()
        
        # In case the server sent a relative URL, join with original:
        newurl = urlparse.urljoin(self.type + ":" + url, newurl)
        return self.open(newurl)
    
# The following is based on Python's urllib.py "URLopener.retrieve"
# Also based on http://mail.python.org/pipermail/python-list/2001-October/110069.html

    def http_error_206(self, url, fp, errcode, errmsg, headers, data=None):
        # The next line is taken from urllib's URLopener.open_http
        # method, at the end after the line "if errcode == 200:"
        return urllib.addinfourl(fp, headers, 'http:' + url)

    def retrieve_resume(self, url, filename, reporthook=None, data=None):
        """Download files from an URL; return (headers, real_url)

        Resumes a download if the local filename exists and
        the server supports download resuming.
        """

        current_size = 0
        tfp = None
        if os.path.exists(filename):
            try:
                current_size = os.path.getsize(filename)
                tfp = open(filename, 'ab')
                #If the file exists, then only download the remainder
                if current_size > 0:
                    self.addheader('Range', 'bytes=%s-' % (current_size))
            except:
                log('Cannot open file for resuming: %s', filename, sender=self, traceback=True)
                tfp = None
                current_size = 0

        if tfp is None:
            tfp = open(filename, 'wb')

        # Fix a problem with bad URLs that are not encoded correctly (bug 549)
        url = url.decode('ascii', 'ignore')
        url = url.translate(self.ESCAPE_CHARS)
        url = url.encode('ascii')

        url = urllib.unwrap(urllib.toBytes(url))
        fp = self.open(url, data)
        headers = fp.info()

        if current_size > 0:
            # We told the server to resume - see if she agrees
            # See RFC2616 (206 Partial Content + Section 14.16)
            # XXX check status code here, too...
            range = ContentRange.parse(headers.get('content-range', ''))
            if range is None or range.start != current_size:
                # Ok, that did not work. Reset the download
                # TODO: seek and truncate if content-range differs from request
                tfp.close()
                tfp = open(filename, 'wb')
                current_size = 0
                log('Cannot resume. Missing or wrong Content-Range header (RFC2616)', sender=self)

        result = headers, fp.geturl()
        bs = 1024*8
        size = -1
        read = current_size
        blocknum = int(current_size/bs)
        if reporthook:
            if "content-length" in headers:
                size = int(headers.getrawheader("Content-Length"))  + current_size
            reporthook(blocknum, bs, size)
        while read < size or size == -1:
            if size == -1:
                block = fp.read(bs)
            else:
                block = fp.read(min(size-read, bs))
            if block == "":
                break
            read += len(block)
            tfp.write(block)
            blocknum += 1
            if reporthook:
                reporthook(blocknum, bs, size)
        fp.close()
        tfp.close()
        del fp
        del tfp

        # raise exception if actual size does not match content-length header
        if size >= 0 and read < size:
            raise urllib.ContentTooShortError("retrieval incomplete: got only %i out "
                                       "of %i bytes" % (read, size), result)

        return result

# end code based on urllib.py

    def prompt_user_passwd( self, host, realm):
        # Keep track of authentication attempts, fail after the third one
        self._auth_retry_counter += 1
        if self._auth_retry_counter > 3:
            raise AuthenticationError(_('Wrong username/password'))

        if self.channel.username or self.channel.password:
            log( 'Authenticating as "%s" to "%s" for realm "%s".', self.channel.username, host, realm, sender = self)
            return ( self.channel.username, self.channel.password )

        return (None, None)


class DownloadQueueWorker(threading.Thread):
    def __init__(self, queue, exit_callback, continue_check_callback, minimum_tasks):
        threading.Thread.__init__(self)
        self.queue = queue
        self.exit_callback = exit_callback
        self.continue_check_callback = continue_check_callback

        # The minimum amount of tasks that should be downloaded by this worker
        # before using the continue_check_callback to determine if it might
        # continue accepting tasks. This can be used to forcefully start a
        # download, even if a download limit is in effect.
        self.minimum_tasks = minimum_tasks

    def run(self):
        log('Running new thread: %s', self.getName(), sender=self)
        while True:
            # Check if this thread is allowed to continue accepting tasks
            # (But only after reducing minimum_tasks to zero - see above)
            if self.minimum_tasks > 0:
                self.minimum_tasks -= 1
            elif not self.continue_check_callback(self):
                log('%s must not accept new tasks.', self.getName(), sender=self)
                return

            try:
                task = self.queue.pop()
                log('%s is processing: %s', self.getName(), task, sender=self)
                task.run()
            except IndexError, e:
                log('No more tasks for %s to carry out.', self.getName(), sender=self)
                break
        self.exit_callback(self)


class DownloadQueueManager(object):
    def __init__(self, config):
        self._config = config
        self.tasks = collections.deque()

        self.worker_threads_access = threading.RLock()
        self.worker_threads = []

    def __exit_callback(self, worker_thread):
        with self.worker_threads_access:
            self.worker_threads.remove(worker_thread)

    def __continue_check_callback(self, worker_thread):
        with self.worker_threads_access:
            if len(self.worker_threads) > self._config.max_downloads and \
                    self._config.max_downloads_enabled:
                self.worker_threads.remove(worker_thread)
                return False
            else:
                return True

    def spawn_threads(self, force_start=False):
        """Spawn new worker threads if necessary

        If force_start is True, forcefully spawn a thread and
        let it process at least one episodes, even if a download
        limit is in effect at the moment.
        """
        with self.worker_threads_access:
            if not len(self.tasks):
                return

            if force_start or len(self.worker_threads) == 0 or \
                    len(self.worker_threads) < self._config.max_downloads or \
                    not self._config.max_downloads_enabled:
                # We have to create a new thread here, there's work to do
                log('I am going to spawn a new worker thread.', sender=self)

                # The new worker should process at least one task (the one
                # that we want to forcefully start) if force_start is True.
                if force_start:
                    minimum_tasks = 1
                else:
                    minimum_tasks = 0

                worker = DownloadQueueWorker(self.tasks, self.__exit_callback, \
                        self.__continue_check_callback, minimum_tasks)
                self.worker_threads.append(worker)
                worker.start()

    def are_queued_or_active_tasks(self):
        with self.worker_threads_access:
            return len(self.worker_threads) > 0

    def add_task(self, task, force_start=False):
        """Add a new task to the download queue

        If force_start is True, ignore the download limit
        and forcefully start the download right away.
        """
        if task.status != DownloadTask.INIT:
            # This task is old so update episode from db
            task.episode.reload_from_db()

            # Remove the task from its current position in the
            # download queue (if any) to avoid race conditions
            # where two worker threads download the same file
            try:
                self.tasks.remove(task)
            except ValueError, e:
                pass
        task.status = DownloadTask.QUEUED
        if force_start:
            # Add the task to be taken on next pop
            self.tasks.append(task)
        else:
            # Add the task to the end of the queue
            self.tasks.appendleft(task)
        self.spawn_threads(force_start)


class DownloadTask(object):
    """An object representing the download task of an episode

    You can create a new download task like this:

        task = DownloadTask(episode, gpodder.config.Config(CONFIGFILE))
        task.status = DownloadTask.QUEUED
        task.run()

    While the download is in progress, you can access its properties:

        task.total_size       # in bytes
        task.progress         # from 0.0 to 1.0
        task.speed            # in bytes per second
        str(task)             # name of the episode
        task.status           # current status
        task.status_changed   # True if the status has been changed (see below)
        task.url              # URL of the episode being downloaded
        task.podcast_url      # URL of the podcast this download belongs to

    You can cancel a running download task by setting its status:

        task.status = DownloadTask.CANCELLED

    The task will then abort as soon as possible (due to the nature
    of downloading data, this can take a while when the Internet is
    busy).

    The "status_changed" attribute gets set to True everytime the
    "status" attribute changes its value. After you get the value of
    the "status_changed" attribute, it is always reset to False:

        if task.status_changed:
            new_status = task.status
            # .. update the UI accordingly ..

    Obviously, this also means that you must have at most *one*
    place in your UI code where you check for status changes and
    broadcast the status updates from there.

    While the download is taking place and after the .run() method
    has finished, you can get the final status to check if the download
    was successful:

        if task.status == DownloadTask.DONE:
            # .. everything ok ..
        elif task.status == DownloadTask.FAILED:
            # .. an error happened, and the
            #    error_message attribute is set ..
            print task.error_message
        elif task.status == DownloadTask.PAUSED:
            # .. user paused the download ..
        elif task.status == DownloadTask.CANCELLED:
            # .. user cancelled the download ..

    The difference between cancelling and pausing a DownloadTask is
    that the temporary file gets deleted when cancelling, but does
    not get deleted when pausing.

    Be sure to call .removed_from_list() on this task when removing
    it from the UI, so that it can carry out any pending clean-up
    actions (e.g. removing the temporary file when the task has not
    finished successfully; i.e. task.status != DownloadTask.DONE).

    The UI can call the method "notify_as_finished()" to determine if
    this episode still has still to be shown as "finished" download
    in a notification window. This will return True only the first time
    it is called when the status is DONE. After returning True once,
    it will always return False afterwards.

    The same thing works for failed downloads ("notify_as_failed()").
    """
    # Possible states this download task can be in
    STATUS_MESSAGE = (_('Added'), _('Queued'), _('Downloading'),
            _('Finished'), _('Failed'), _('Cancelled'), _('Paused'))
    (INIT, QUEUED, DOWNLOADING, DONE, FAILED, CANCELLED, PAUSED) = range(7)

    def __str__(self):
        return self.__episode.title

    def __get_status(self):
        return self.__status

    def __set_status(self, status):
        if status != self.__status:
            self.__status_changed = True
            self.__status = status

    status = property(fget=__get_status, fset=__set_status)

    def __get_status_changed(self):
        if self.__status_changed:
            self.__status_changed = False
            return True
        else:
            return False

    status_changed = property(fget=__get_status_changed)

    def __get_url(self):
        return self.__episode.url

    url = property(fget=__get_url)

    def __get_podcast_url(self):
        return self.__episode.channel.url

    podcast_url = property(fget=__get_podcast_url)

    def __get_episode(self):
        return self.__episode

    episode = property(fget=__get_episode)

    def removed_from_list(self):
        if self.status != self.DONE:
            util.delete_file(self.tempname)

    def __init__(self, episode, config):
        self.__status = DownloadTask.INIT
        self.__status_changed = True
        self.__episode = episode
        self._config = config

        # Set names for the downloads list
        self.markup_name = saxutils.escape(self.__episode.title)
        self.markup_podcast_name = saxutils.escape(self.__episode.channel.title)

        # Create the target filename and save it in the database
        self.filename = self.__episode.local_filename(create=True)
        self.tempname = self.filename + '.partial'

        self.total_size = self.__episode.length
        self.speed = 0.0
        self.progress = 0.0
        self.error_message = None

        # Have we already shown this task in a notification?
        self._notification_shown = False

        # Variables for speed limit and speed calculation
        self.__start_time = 0
        self.__start_blocks = 0
        self.__limit_rate_value = self._config.limit_rate_value
        self.__limit_rate = self._config.limit_rate

        # Callbacks
        self._progress_updated = lambda x: None

        # If the tempname already exists, set progress accordingly
        if os.path.exists(self.tempname):
            try:
                already_downloaded = os.path.getsize(self.tempname)
                if self.total_size > 0:
                    self.progress = max(0.0, min(1.0, float(already_downloaded)/self.total_size))
            except OSError, os_error:
                log('Error while getting size for existing file: %s', os_error, sender=self)
        else:
            # "touch self.tempname", so we also get partial
            # files for resuming when the file is queued
            open(self.tempname, 'w').close()

    def notify_as_finished(self):
        if self.status == DownloadTask.DONE:
            if self._notification_shown:
                return False
            else:
                self._notification_shown = True
                return True

        return False

    def notify_as_failed(self):
        if self.status == DownloadTask.FAILED:
            if self._notification_shown:
                return False
            else:
                self._notification_shown = True
                return True

        return False

    def add_progress_callback(self, callback):
        self._progress_updated = callback

    def status_updated(self, count, blockSize, totalSize):
        # We see a different "total size" while downloading,
        # so correct the total size variable in the thread
        if totalSize != self.total_size and totalSize > 0:
            self.total_size = float(totalSize)

        if self.total_size > 0:
            self.progress = max(0.0, min(1.0, float(count*blockSize)/self.total_size))
            self._progress_updated(self.progress)

        self.calculate_speed(count, blockSize)

        if self.status == DownloadTask.CANCELLED:
            raise DownloadCancelledException()

        if self.status == DownloadTask.PAUSED:
            raise DownloadCancelledException()

    def calculate_speed(self, count, blockSize):
        if count % 5 == 0:
            now = time.time()
            if self.__start_time > 0:
                # Has rate limiting been enabled or disabled?                
                if self.__limit_rate != self._config.limit_rate: 
                    # If it has been enabled then reset base time and block count                    
                    if self._config.limit_rate:
                        self.__start_time = now
                        self.__start_blocks = count
                    self.__limit_rate = self._config.limit_rate
                    
                # Has the rate been changed and are we currently limiting?            
                if self.__limit_rate_value != self._config.limit_rate_value and self.__limit_rate: 
                    self.__start_time = now
                    self.__start_blocks = count
                    self.__limit_rate_value = self._config.limit_rate_value

                passed = now - self.__start_time
                if passed > 0:
                    speed = ((count-self.__start_blocks)*blockSize)/passed
                else:
                    speed = 0
            else:
                self.__start_time = now
                self.__start_blocks = count
                passed = now - self.__start_time
                speed = count*blockSize

            self.speed = float(speed)

            if self._config.limit_rate and speed > self._config.limit_rate_value:
                # calculate the time that should have passed to reach
                # the desired download rate and wait if necessary
                should_have_passed = float((count-self.__start_blocks)*blockSize)/(self._config.limit_rate_value*1024.0)
                if should_have_passed > passed:
                    # sleep a maximum of 10 seconds to not cause time-outs
                    delay = min(10.0, float(should_have_passed-passed))
                    time.sleep(delay)

    def run(self):
        # Speed calculation (re-)starts here
        self.__start_time = 0
        self.__start_blocks = 0

        # If the download has already been cancelled, skip it
        if self.status == DownloadTask.CANCELLED:
            util.delete_file(self.tempname)
            self.progress = 0.0
            self.speed = 0.0
            return False

        # We only start this download if its status is "queued"
        if self.status != DownloadTask.QUEUED:
            return False

        # We are downloading this file right now
        self.status = DownloadTask.DOWNLOADING
        self._notification_shown = False

        try:
            # Resolve URL and start downloading the episode
            url = youtube.get_real_download_url(self.__episode.url, \
                    self._config.youtube_preferred_fmt_id)
            downloader =  DownloadURLOpener(self.__episode.channel)
            headers, real_url = downloader.retrieve_resume(url, \
                    self.tempname, reporthook=self.status_updated)

            new_mimetype = headers.get('content-type', self.__episode.mimetype)
            old_mimetype = self.__episode.mimetype
            _basename, ext = os.path.splitext(self.filename)
            if new_mimetype != old_mimetype or util.wrong_extension(ext):
                log('Correcting mime type: %s => %s', old_mimetype, new_mimetype, sender=self)
                old_extension = self.__episode.extension()
                self.__episode.mimetype = new_mimetype
                new_extension = self.__episode.extension()

                # If the desired filename extension changed due to the new
                # mimetype, we force an update of the local filename to fix the
                # extension.
                if old_extension != new_extension or util.wrong_extension(ext):
                    self.filename = self.__episode.local_filename(create=True, force_update=True)

            # TODO: Check if "real_url" is different from "url" and if it is,
            #       see if we can get a better episode filename out of it

            # Look at the Content-disposition header; use if if available
            disposition_filename = get_header_param(headers, \
                    'filename', 'content-disposition')

            # Some servers do send the content-disposition header, but provide
            # an empty filename, resulting in an empty string here (bug 1440)
            if disposition_filename is not None and disposition_filename != '':
                # The server specifies a download filename - try to use it
                disposition_filename = os.path.basename(disposition_filename)
                self.filename = self.__episode.local_filename(create=True, \
                        force_update=True, template=disposition_filename)
                new_mimetype, encoding = mimetypes.guess_type(self.filename)
                if new_mimetype is not None:
                    log('Using content-disposition mimetype: %s',
                            new_mimetype, sender=self)
                    self.__episode.set_mimetype(new_mimetype, commit=True)

            shutil.move(self.tempname, self.filename)

            # Model- and database-related updates after a download has finished
            self.__episode.on_downloaded(self.filename)

            # If a user command has been defined, execute the command setting some environment variables
            if len(self._config.cmd_download_complete) > 0:
                os.environ["GPODDER_EPISODE_URL"]=self.__episode.url or ''
                os.environ["GPODDER_EPISODE_TITLE"]=self.__episode.title or ''
                os.environ["GPODDER_EPISODE_FILENAME"]=self.filename or ''
                os.environ["GPODDER_EPISODE_PUBDATE"]=str(int(self.__episode.pubDate))
                os.environ["GPODDER_EPISODE_LINK"]=self.__episode.link or ''
                os.environ["GPODDER_EPISODE_DESC"]=self.__episode.description or ''
                os.environ["GPODDER_CHANNEL_TITLE"]=self.__episode.channel.title or ''
                util.run_external_command(self._config.cmd_download_complete)
        except DownloadCancelledException:
            log('Download has been cancelled/paused: %s', self, sender=self)
            if self.status == DownloadTask.CANCELLED:
                util.delete_file(self.tempname)
                self.progress = 0.0
                self.speed = 0.0
        except urllib.ContentTooShortError, ctse:
            self.status = DownloadTask.FAILED
            self.error_message = _('Missing content from server')
        except IOError, ioe:
            log( 'Error "%s" while downloading "%s": %s', ioe.strerror, self.__episode.title, ioe.filename, sender=self, traceback=True)
            self.status = DownloadTask.FAILED
            d = {'error': ioe.strerror, 'filename': ioe.filename}
            self.error_message = _('I/O Error: %(error)s: %(filename)s') % d
        except gPodderDownloadHTTPError, gdhe:
            log( 'HTTP error %s while downloading "%s": %s', gdhe.error_code, self.__episode.title, gdhe.error_message, sender=self)
            self.status = DownloadTask.FAILED
            d = {'code': gdhe.error_code, 'message': gdhe.error_message}
            self.error_message = _('HTTP Error %(code)s: %(message)s') % d
        except Exception, e:
            self.status = DownloadTask.FAILED
            log('Download error: %s', str(e), traceback=True, sender=self)
            self.error_message = _('Error: %s') % (str(e),)

        if self.status == DownloadTask.DOWNLOADING:
            # Everything went well - we're done
            self.status = DownloadTask.DONE
            if self.total_size <= 0:
                self.total_size = util.calculate_size(self.filename)
                log('Total size updated to %d', self.total_size, sender=self)
            self.progress = 1.0
            if gpodder.user_hooks is not None:
                gpodder.user_hooks.on_episode_downloaded(self.__episode)
            return True
        
        self.speed = 0.0

        # We finished, but not successfully (at least not really)
        return False

