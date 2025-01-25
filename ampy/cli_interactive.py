# Adafruit MicroPython Tool - Command Line Interface
# Author: Tony DiCola
# Copyright (c) 2016 Adafruit Industries
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from __future__ import print_function
import os
import platform
import posixpath
import re
import serial.serialutil

import click
import dotenv
from progress_bar import PorgressBar

# Load AMPY_PORT et al from .ampy file
# Performed here because we need to beat click's decorators.
config = dotenv.find_dotenv(filename=".ampy", usecwd=True)
if config:
    dotenv.load_dotenv(dotenv_path=config)

import ampy.files as files
import ampy.pyboard as pyboard


# On Windows fix the COM port path name for ports above 9 (see comment in
# windows_full_port_name function).
port = "/dev/ttyACM0"
baud = 115200
delay = 0
history = []
pico_wd = '/'
queued_cmd = None
cmd = None


if platform.system() == "Windows":
    port = windows_full_port_name(port)
_board = pyboard.Pyboard(port, baudrate=baud, rawdelay=delay)

def windows_full_port_name(portname):
    # Helper function to generate proper Windows COM port paths.  Apparently
    # Windows requires COM ports above 9 to have a special path, where ports below
    # 9 are just referred to by COM1, COM2, etc. (wacky!)  See this post for
    # more info and where this code came from:
    # http://eli.thegreenplace.net/2009/07/31/listing-all-serial-ports-on-windows-with-python/
    m = re.match(r"^COM(\d+)$", portname)
    if m and int(m.group(1)) < 10:
        return portname
    else:
        return "\\\\.\\{0}".format(portname)


def get(remote_file, local_file):
    """
    Retrieve a file from the board.

    Get will download a file from the board and print its contents or save it
    locally.  You must pass at least one argument which is the path to the file
    to download from the board.  If you don't specify a second argument then
    the file contents will be printed to standard output.  However if you pass
    a file name as the second argument then the contents of the downloaded file
    will be saved to that file (overwriting anything inside it!).

    For example to retrieve the boot.py and print it out run:

      ampy --port /board/serial/port get boot.py

    Or to get main.py and save it as main.py locally run:

      ampy --port /board/serial/port get main.py main.py
    """
    # Get the file contents.
    board_files = files.Files(_board)
    contents = board_files.get(remote_file)
    # Print the file out if no local file was provided, otherwise save it.
    if local_file is None:
        return contents.decode("utf-8")
    else:
        local_file.write(contents)

def mkdir(directory, exists_okay, make_parents):
    """
    Create a directory on the board.

    Mkdir will create the specified directory on the board.  One argument is
    required, the full path of the directory to create.

    By default you cannot recursively create a hierarchy of directories with one
    mkdir command. You may create each parent directory with separate
    mkdir command calls, or use the --make-parents option.
    
    For example to make a directory under the root called 'code':

      ampy --port /board/serial/port mkdir /code
      
    To make a directory under the root called 'code/for/ampy', along with all
    missing parents:

      ampy --port /board/serial/port mkdir --make-parents /code/for/ampy
    """
    # Run the mkdir command.
    board_files = files.Files(_board)
    if make_parents:
        if directory[0] != '/':
            directory = "/" + directory
        dirpath = ""
        for dir in directory.split("/")[1:-1]:
            dirpath += "/" + dir
            board_files.mkdir(dirpath, exists_okay=True)
    board_files.mkdir(directory, exists_okay=exists_okay)

def ls(directory):
    """List contents of a directory on the board.

    Can pass an optional argument which is the path to the directory.  The
    default is to list the contents of the root, /, path.

    For example to list the contents of the root run:

      ampy --port /board/serial/port ls

    Or to list the contents of the /foo/bar directory on the board run:

      ampy --port /board/serial/port ls /foo/bar

    Add the -l or --long_format flag to print the size of files (however note
    MicroPython does not calculate the size of folders and will show 0 bytes):

      ampy --port /board/serial/port ls -l /foo/bar
      """
    # List each file/directory on a separate line.
    board_files = files.Files(_board)
    dlist = []
    for f in board_files.lsi(directory):
        d = f[0].split('/')[-1] # file or dir name
        if f[1] == 0x4000: # a directory
            dlist.insert(0, "+ " + d)
        else:
            dlist.append("- " + d)
    return dlist

def put(local, remote):
    """Put a file or folder and its contents on the board.

    Put will upload a local file or folder  to the board.  If the file already
    exists on the board it will be overwritten with no warning!  You must pass
    at least one argument which is the path to the local file/folder to
    upload.  If the item to upload is a folder then it will be copied to the
    board recursively with its entire child structure.  You can pass a second
    optional argument which is the path and name of the file/folder to put to
    on the connected board.

    For example to upload a main.py from the current directory to the board's
    root run:

      ampy --port /board/serial/port put main.py

    Or to upload a board_boot.py from a ./foo subdirectory and save it as boot.py
    in the board's root run:

      ampy --port /board/serial/port put ./foo/board_boot.py boot.py

    To upload a local folder adafruit_library and all of its child files/folders
    as an item under the board's root run:

      ampy --port /board/serial/port put adafruit_library

    Or to put a local folder adafruit_library on the board under the path
    /lib/adafruit_library on the board run:

      ampy --port /board/serial/port put adafruit_library /lib/adafruit_library
    """
    # Use the local filename if no remote filename is provided.
    if remote is None:
        remote = os.path.basename(os.path.abspath(local))
    # Check if path is a folder and do recursive copy of everything inside it.
    # Otherwise it's a file and should simply be copied over.
    if os.path.isdir(local):
        # Create progress bar for each file
        pb_bath =  ProgressBar('Overall progress')
        for parent, child_dirs, child_files in os.walk(local, followlinks=True):
            for filename in child_files:
                path = os.path.join(parent, filename)
                size = os.stat(path).st_size
                pb_bath.add_subjob(PorgressBar(name=path,total=size ))

        # Directory copy, create the directory and walk all children to copy
        # over the files.
        board_files = files.Files(_board)
        for parent, child_dirs, child_files in os.walk(local, followlinks=True):
            # Create board filesystem absolute path to parent directory.
            remote_parent = posixpath.normpath(
                posixpath.join(remote, os.path.relpath(parent, local))
            )
            try:
                # Create remote parent directory.
                board_files.mkdir(remote_parent)
            except files.DirectoryExistsError:
                # Ignore errors for directories that already exist.
                pass
            
            # Loop through all the files and put them on the board too.
            for filename in child_files:
                local_path = os.path.join(parent, filename)
                with open(local_path, "rb") as infile:
                    remote_filename = posixpath.join(remote_parent, filename)
                    data = infile.read()
                    job = pb_bath.get_subjob(local_path)
                    callback = job.on_progress_done
                    board_files.put(remote_filename, data, callback)
    else:
        # File copy, open the file and copy its contents to the board.
        # Put the file on the board.
        with open(local, "rb") as infile:
            data = infile.read()
            progress = PorgressBar(name=local, total=len(data))
            board_files = files.Files(_board)
            board_files.put(remote, data, progress.on_progress_done)
    print('')

def rm(remote_file):
    """Remove a file from the board.

    Remove the specified file from the board's filesystem.  Must specify one
    argument which is the path to the file to delete.  Note that this can't
    delete directories which have files inside them, but can delete empty
    directories.

    For example to delete main.py from the root of a board run:

      ampy --port /board/serial/port rm main.py
    """
    # Delete the provided file/directory on the board.
    board_files = files.Files(_board)
    board_files.rm(remote_file)

def rmdir(remote_folder, missing_okay):
    """Forcefully remove a folder and all its children from the board.

    Remove the specified folder from the board's filesystem.  Must specify one
    argument which is the path to the folder to delete.  This will delete the
    directory and ALL of its children recursively, use with caution!

    If missing_okay: ignore if directory does not exist

    For example to delete everything under /adafruit_library from the root of a
    board run:

      ampy --port /board/serial/port rmdir adafruit_library
    """
    # Delete the provided file/directory on the board.
    board_files = files.Files(_board)
    board_files.rmdir(remote_folder, missing_okay=missing_okay)

def run(local_file, no_output):
    """Run a script and print its output.
    
    no_output: suppress output for scripts with infinite loops or no output

    Run will send the specified file to the board and execute it immediately.
    Any output from the board will be printed to the console (note that this is
    not a 'shell' and you can't send input to the program).

    Note that if your code has a main or infinite loop you should add the --no-output
    option.  This will run the script and immediately exit without waiting for
    the script to finish and print output.

    For example to run a test.py script and print any output until it finishes:

      ampy --port /board/serial/port run test.py

    Or to run test.py and not wait for it to finish:

      ampy --port /board/serial/port run --no-output test.py
    """
    # Run the provided file and print its output.
    board_files = files.Files(_board)
    try:
        output = board_files.run(local_file, not no_output, not no_output)
        if output is not None:
            print(output.decode("utf-8"), end="")
    except IOError:
        click.echo(
            "Failed to find or read input file: {0}".format(local_file), err=True
        )

def reset(mode):
    """Perform soft reset/reboot of the board.

    Will connect to the board and perform a reset.  Depending on the board
    and firmware, several different types of reset may be supported.

      ampy --port /board/serial/port reset

    modes: SAFE_MODE, SOFT (to REPL), NORMAL (runs init.py), BOOTLOADER
    """
    _board.enter_raw_repl()
    if mode == "SOFT":
        _board.exit_raw_repl()
        return

    _board.exec_(
        """if 1:
        def on_next_reset(x):
            try:
                import microcontroller
            except:
                if x == 'NORMAL': return ''
                return 'Reset mode only supported on CircuitPython'
            try:
                microcontroller.on_next_reset(getattr(microcontroller.RunMode, x))
            except ValueError as e:
                return str(e)
            return ''
        def reset():
            try:
                import microcontroller
            except:
                import machine as microcontroller
            microcontroller.reset()
    """
    )
    r = _board.eval("on_next_reset({})".format(repr(mode)))
    print("here we are", repr(r))
    if r:
        click.echo(r, err=True)
        return

    try:
        _board.exec_raw_no_follow("reset()")
    except serial.serialutil.SerialException as e:
        # An error is expected to occur, as the board should disconnect from
        # serial when restarted via microcontroller.reset()
        pass

def parse_dir(my_d: str):
    d = my_d
    if d == '..':
        d = pico_wd.split('/')[:-2]
        d = '/'.join(d) if len(d) > 1 else '/'
    elif d[:3] == '../':
        root = pico_wd.split('/')[:-2]
        root = '/'.join(root) if len(root) > 1 else ''
        d = root + d[2:]
    elif d[0] != '/':
        d = pico_wd + d

    if d == '//':
        d = '/'
    print(f"Returning {d}")
    return d

if __name__ == "__main__":
    while True:
        if pico_wd[-1] != '/':
            pico_wd = pico_wd + '/'

        #get, mkdir, ls, *cd, put, rm, rmdir, run, reset
        #lsl, pwd, cdl, edit, repl, port, history
        query = input(f"ampy in {pico_wd} >>> ")
        tokens = query.split(" ")
        print(tokens)
        command = tokens[0]
        params = tokens[1:]
        if command == "get" or command == "get!":
            remote, local = None, None
            if len(params) == 2:
                remote, local = params[0], params[1]
            elif len(params) == 1:
                remote = params[0]
            else:
                print("malformed command")
                # TODO implement help message
            try:
                if local is not None:
                    flags = 'xb'
                    if command == "get!":
                        flags = 'wb'
                    with open(local, flags) as f:
                        get(remote, f)
                else:
                    get(remote, None)
            except RuntimeError as e:
                print(e)
            except FileExistsError:
                print("Local file already exists. Use get! to overwrite.")
        elif command in ["put", "put!"]:
            local, remote = None, None
            if len(params) == 2:
                local, remote = params[0], params[1]
            elif len(params) == 1:
                local = remote = params[0]

            file_exists = True
            try:
                file = get(remote, None)
            except RuntimeError as e:
                if "No such" in str(e):
                    file_exists = False
                else:
                    raise e

            try:
                if local is not None and file_exists == False or command == "put!":
                    put(local, remote)
                else:
                    if local is None:
                        print("No file specified!") #TODO
                    else:
                        print("File exists on device. Use put! to overwrite.")
            except RuntimeError as e:
                print(e)
            except FileNotFoundError as e:
                print(e)


        elif command == "mkdir":
            if len(params) > 0:
                for d in params:
                    try:
                        mkdir(parse_dir(d), False, True)
                    except files.DirectoryExistsError:
                        print(f"Directory already exists: {d}")
            else:
                print("malformed command")
                # TODO help message
        elif command == "ls":
            # TODO: handle malformed commands
            d = None
            if len(params) == 1:
                d = parse_dir(params[0])
                print(d)
            elif len(params) == 0:
                d = pico_wd
            if d[0] != '/':
                d = pico_wd + d
            if d is not None:
                print(f"Trying to ls {d}")
                try:
                    for f in ls(d):
                        print(f)
                except RuntimeError as e:
                    print(e)
        elif command == "cd":
            if len(params) == 0:
                pico_wd = '/'
            elif len(params) == 1:
                d = params[0]
                if d[0] != '/':
                    if d == '..':
                        d = pico_wd.split('/')[:-2]
                        d = '/'.join(d) if len(d) > 1 else '/'
                    else:
                        d = pico_wd + d
                print(f"Trying to cd into {d}")
                try:
                    x = ls(d)
                    pico_wd = d
                except RuntimeError as e:
                    print(e)
                    
            else:
                print("Malformed command")


    # Try to ensure the board serial connection is always gracefully closed.
    if _board is not None:
        try:
            _board.close()
        except:
            # Swallow errors when attempting to close as it's just a best effort
            # and shouldn't cause a new error or problem if the connection can't
            # be closed.
            pass