"""This module contains all CUDA specific kernel_tuner functions"""
import numpy

#embedded in try block to be able to generate documentation
#and run tests without pycuda installed
try:
    import pycuda.driver as drv
    from pycuda.compiler import SourceModule
except Exception as e:
    drv = object()
    SourceModule = object()



class CudaFunctions(object):
    """Class that groups the CUDA functions on maintains state about the device"""

    def __init__(self, device=0, iterations=7):
        """instantiate CudaFunctions object used for interacting with the CUDA device

        Instantiating this object will inspect and store certain device properties at
        runtime, which are used during compilation and/or execution of kernels by the
        kernel tuner. It also maintains a reference to the most recently compiled
        source module for copying data to constant memory before kernel launch.

        :param device: Number of CUDA device to use for this context
        :type device: int

        :param iterations: Number of iterations used while benchmarking a kernel, 7 by default.
        :type iterations: int
        """
        drv.init()
        self.context = drv.Device(device).make_context()

        #inspect device properties
        devprops = { str(k): v for (k, v) in self.context.get_device().get_attributes().items() }
        self.max_threads = devprops['MAX_THREADS_PER_BLOCK']
        self.cc = str(devprops['COMPUTE_CAPABILITY_MAJOR']) + str(devprops['COMPUTE_CAPABILITY_MINOR'])
        self.ITERATIONS = iterations
        self.current_module = None


    def __del__(self):
        if (self.context is not None):
            self.context.detach()


    def create_gpu_args(self, arguments):
        """ready argument list to be passed to the kernel, allocates gpu mem

        :param arguments: List of arguments to be passed to the kernel.
            The order should match the argument list on the CUDA kernel.
            Allowed values are numpy.ndarray, and/or numpy.int32, numpy.float32, and so on.
        :type arguments: list(numpy objects)

        :returns: A list of arguments that can be passed to an CUDA kernel.
        :rtype: list( pycuda.driver.DeviceAllocation, numpy.int32, ... )
        """
        gpu_args = []
        for arg in arguments:
            # if arg i is a numpy array copy to device
            if isinstance(arg, numpy.ndarray):
                gpu_args.append(drv.mem_alloc(arg.nbytes))
                drv.memcpy_htod(gpu_args[-1], arg)
            else: # if not an array, just pass argument along
                gpu_args.append(arg)
        return gpu_args

    def cleanup_gpu_args(self, arguments):
        for arg in arguments:
            if isinstance(arg, drv.DeviceAllocation):
                arg.free()



    def compile(self, kernel_name, kernel_string):
        """call the CUDA compiler to compile the kernel, return the device function

        :param kernel_name: The name of the kernel to be compiled, used to lookup the
            function after compilation.
        :type kernel_name: string

        :param kernel_string: The CUDA kernel code that contains the function `kernel_name`
        :type kernel_string: string

        :returns: An CUDA kernel that can be called directly.
        :rtype: pycuda.driver.Function
        """
        try:
            self.current_module = SourceModule(kernel_string, options=['-Xcompiler=-Wall'],
                    arch='compute_' + self.cc, code='sm_' + self.cc,
                    cache_dir=False)
            func = self.current_module.get_function(kernel_name)
            return func
        except drv.CompileError as e:
            if "uses too much shared data" in e.stderr:
                raise Exception("uses too much shared data")
            else:
                raise e


    def benchmark(self, func, gpu_args, threads, grid):
        """runs the kernel and measures time repeatedly, returns average time

        Runs the kernel and measures kernel execution time repeatedly, number of
        iterations is set during the creation of CudaFunctions. Benchmark returns
        a robust average, from all measurements the fastest and slowest runs are
        discarded and the rest is included in the returned average. The reason for
        this is to be robust against initialization artifacts and other exceptional
        cases.

        :param func: A PyCuda kernel compiled for this specific kernel configuration
        :type func: pycuda.driver.Function

        :param gpu_args: A list of arguments to the kernel, order should match the
            order in the code. Allowed values are either variables in global memory
            or single values passed by value.
        :type gpu_args: list( pycuda.driver.DeviceAllocation, numpy.int32, ...)

        :param threads: A tuple listing the number of threads in each dimension of
            the thread block
        :type threads: tuple(int, int, int)

        :param grid: A tuple listing the number of thread blocks in each dimension
            of the grid
        :type grid: tuple(int, int)

        :returns: A robust average for the kernel execution time.
        :rtype: float
        """
        start = drv.Event()
        end = drv.Event()
        times = []
        for _ in range(self.ITERATIONS):
            self.context.synchronize()
            start.record()
            self.run_kernel(func, gpu_args, threads, grid)
            end.record()
            self.context.synchronize()
            times.append(end.time_since(start))
        times = sorted(times)
        return numpy.mean(times[1:-1])

    def run_kernel(self, func, gpu_args, threads, grid):
        func(*gpu_args, block=threads, grid=grid)

    def copy_constant_memory_args(self, cmem_args):
        """adds constant memory arguments to the most recently compiled module

        :param cmem_args: A dictionary containing the data to be passed to the
            device constant memory. The format to be used is as follows: A
            string key is used to name the constant memory symbol to which the
            value needs to be copied. Similar to regular arguments, these need
            to be numpy objects, such as numpy.ndarray or numpy.int32, and so on.
        :type cmem_args: dict( string: numpy.ndarray, ... )
        """
        for k,v in cmem_args.items():
            symbol = self.current_module.get_global(k)[0]
            drv.memcpy_htod(symbol, v)

    def memset(self, allocation, value, size):
        drv.memset_d32(allocation, value, size)

    def memcpy_dtoh(self, dest, src):
        drv.memcpy_dtoh(dest, src)
