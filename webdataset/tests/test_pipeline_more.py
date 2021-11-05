import io
import os
import numpy as np
import PIL
import pytest
import torch
import pickle

import webdataset as wds
import webdataset.extradatasets as eds
from webdataset import autodecode
from webdataset import handlers
from webdataset import dataset
from webdataset import shardlists
from torch.utils.data import DataLoader


local_data = "testdata/imagenet-000000.tgz"
compressed = "testdata/compressed.tar"
remote_loc = "http://storage.googleapis.com/nvdata-openimages/"
remote_shards = "openimages-train-0000{00..99}.tar"
remote_shard = "openimages-train-000321.tar"
remote_pattern = "openimages-train-{}.tar"


def identity(x):
    return x


def count_samples_tuple(source, *args, n=10000):
    count = 0
    for i, sample in enumerate(iter(source)):
        if i >= n:
            break
        assert isinstance(sample, (tuple, dict, list)), (type(sample), sample)
        for f in args:
            assert f(sample)
        count += 1
    return count


def count_samples(source, *args, n=1000):
    count = 0
    for i, sample in enumerate(iter(source)):
        if i >= n:
            break
        for f in args:
            assert f(sample)
        count += 1
    return count


def test_dataset():
    ds = wds.DataPipeline(wds.SimpleShardList(local_data), wds.tarfile_to_samples())
    assert count_samples_tuple(ds) == 47


def test_dataset_resampled():
    ds = wds.DataPipeline(wds.ResampledShards(local_data), wds.tarfile_to_samples())
    assert count_samples_tuple(ds, n=100) == 100


shardspec = """
datasets:

  - name: CDIP
    perepoch: 10
    buckets:
      - ./gs/nvdata-ocropus/words/
    shards:
    - cdipsub-{000000..000092}.tar

  - name: Google 1000 Books
    perepoch: 20
    buckets:
      - ./gs/nvdata-ocropus/words/
    shards:
      - gsub-{000000..000167}.tar

  - name: Internet Archive Sample
    perepoch: 30
    buckets:
      - ./gs/nvdata-ocropus/words/
    shards:
      - ia1-{000000..000033}.tar
"""


def test_yaml(tmp_path):
    tmp_path = str(tmp_path)
    fname = tmp_path + "/test.shards.yml"
    with open(fname, "w") as stream:
        stream.write(shardspec)
    ds = dataset.MultiShardSample(fname)
    l = ds.sample()
    assert len(l) == 60, len(l)


dsspec = """
prefix: ""
epoch: 111

datasets:

  - name: images
    chunksize: 10
    resampled: True
    subsample: 0.5
    nworkers: -1
    buckets:
      - ./testdata/
    shards:
      - imagenet-000000.tgz
      - imagenet-000000.tgz
"""


def IGNORE_test_dsspec(tmp_path):
    tmp_path = str(tmp_path)
    fname = tmp_path + "/test.ds.yml"
    with open(fname, "w") as stream:
        stream.write(dsspec)
    ds = dataset.construct_dataset(fname)
    result = [x for x in ds]
    assert len(result) == 111
    assert isinstance(result[0], dict)
    ds = dataset.construct_dataset(fname).decode("rgb").to_tuple("png", "cls")
    result = [x for x in ds]
    assert len(result) == 111
    assert result[0][0].ndim == 3


dsspec2 = """
prefix: ""

datasets:

  - name: images
    nworkers: 2
    shards:
      - ./testdata/imagenet-000000.tgz
  - name: images2
    nworkers: 2
    shards:
      - ./testdata/imagenet-000000.tgz
"""


def IGNORE_test_dsspec2(tmp_path):
    tmp_path = str(tmp_path)
    fname = tmp_path + "/test.ds.yml"
    with open(fname, "w") as stream:
        stream.write(dsspec2)
    ds = dataset.construct_dataset(fname)
    result = [x for x in ds]
    assert len(result) == 2 * 47


def IGNORE_test_log_keys(tmp_path):
    tmp_path = str(tmp_path)
    fname = tmp_path + "/test.ds.yml"
    ds = wds.WebDataset(local_data).log_keys(fname)
    result = [x for x in ds]
    assert len(result) == 47
    with open(fname) as stream:
        lines = stream.readlines()
    assert len(lines) == 47


def IGNORE_test_length():
    ds = wds.WebDataset(local_data)
    with pytest.raises(TypeError):
        len(ds)
    dsl = ds.with_length(1793)
    assert len(dsl) == 1793
    dsl2 = dsl.repeat(17).with_length(19)
    assert len(dsl2) == 19


def test_mock():
    ds = eds.MockDataset((True, True), 193)
    assert count_samples_tuple(ds) == 193


def IGNORE_test_ddp_equalize():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data), wds.tarfile_to_samples(), wds.ddp_equalize(773)
    )
    assert count_samples_tuple(ds) == 733


def test_dataset_shuffle_extract():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.to_tuple("png;jpg cls"),
    )
    assert count_samples_tuple(ds) == 47


def test_dataset_pipe_cat():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.shuffle(5),
        wds.to_tuple("png;jpg cls"),
    )
    assert count_samples_tuple(ds) == 47


def test_slice():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data), wds.tarfile_to_samples(), wds.slice(10)
    )
    assert count_samples_tuple(ds) == 10


def test_dataset_eof():
    import tarfile

    with pytest.raises(tarfile.ReadError):
        ds = wds.DataPipeline(
            wds.SimpleShardList(f"pipe:dd if={local_data} bs=1024 count=10"),
            wds.tarfile_to_samples(),
            wds.shuffle(5),
        )
        assert count_samples(ds) == 47


def test_dataset_eof_handler():
    ds = wds.DataPipeline(
        wds.SimpleShardList(f"pipe:dd if={local_data} bs=1024 count=10"),
        wds.tarfile_to_samples(handler=handlers.ignore_and_stop),
        wds.shuffle(5),
    )
    assert count_samples(ds) < 47


def test_dataset_decode_nohandler():
    count = [0]

    def faulty_decoder(key, data):
        if count[0] % 2 == 0:
            raise ValueError("nothing")
        else:
            return data
        count[0] += 1

    with pytest.raises(ValueError):
        ds = wds.DataPipeline(
            wds.SimpleShardList(local_data),
            wds.tarfile_to_samples(),
            wds.decode(faulty_decoder),
        )
        count_samples_tuple(ds)


def test_dataset_missing_totuple_raises():
    with pytest.raises(ValueError):
        ds = wds.DataPipeline(
            wds.SimpleShardList(f"pipe:dd if={local_data} bs=1024 count=10"),
            wds.tarfile_to_samples(handler=handlers.ignore_and_stop),
            wds.to_tuple("foo", "bar")
        )
        count_samples_tuple(ds)


def test_dataset_missing_rename_raises():
    with pytest.raises(ValueError):
        ds = wds.DataPipeline(
            wds.SimpleShardList(f"pipe:dd if={local_data} bs=1024 count=10"),
            wds.tarfile_to_samples(handler=handlers.ignore_and_stop),
            wds.rename(x="foo", y="bar")
        )
        count_samples_tuple(ds)


def getkeys(sample):
    return set(x for x in sample.keys() if not x.startswith("_"))


def test_dataset_rename_keep():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.rename(image="png", keep=False)
    )
    sample = next(iter(ds))
    assert getkeys(sample) == set(["image"]), getkeys(sample)
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.rename(image="png")
    )
    sample = next(iter(ds))
    assert getkeys(sample) == set("cls image wnid xml".split()), getkeys(sample)


def test_dataset_rsample():

    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.rsample(1.0)
    )
    assert count_samples_tuple(ds) == 47

    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.rsample(0.5)
    )
    result = [count_samples_tuple(ds) for _ in range(300)]
    assert np.mean(result) >= 0.3 * 47 and np.mean(result) <= 0.7 * 47, np.mean(result)


def test_dataset_decode_handler():
    count = [0]
    good = [0]

    def faulty_decoder(key, data):
        if "png" not in key:
            return data
        count[0] += 1
        if count[0] % 2 == 0:
            raise ValueError("nothing")
        else:
            good[0] += 1
            return data

    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.decode(faulty_decoder, handler=handlers.ignore_and_continue),
    )
    result = count_samples_tuple(ds)
    assert count[0] == 47
    assert good[0] == 24
    assert result == 24


def test_dataset_map():
    def f(x):
        assert isinstance(x, dict)
        return x

    def g(x):
        raise ValueError()

    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.map(f),
    )
    count_samples_tuple(ds)

    with pytest.raises(ValueError):
        ds = wds.DataPipeline(
            wds.SimpleShardList(local_data),
            wds.tarfile_to_samples(),
            wds.map(g),
        )
        count_samples_tuple(ds)


def test_dataset_map_dict_handler():

    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.map_dict(png=identity, cls=identity)
    )
    count_samples_tuple(ds)

    with pytest.raises(KeyError):
        ds = wds.DataPipeline(
            wds.SimpleShardList(local_data),
            wds.tarfile_to_samples(),
            wds.map_dict(png=identity, cls2=identity)
        )
        count_samples_tuple(ds)

    def g(x):
        raise ValueError()

    with pytest.raises(ValueError):
        ds = wds.DataPipeline(
            wds.SimpleShardList(local_data),
            wds.tarfile_to_samples(),
            wds.map_dict(png=g, cls2=identity)
        )
        count_samples_tuple(ds)


def test_dataset_shuffle_decode_rename_extract():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.shuffle(5),
        wds.decode("rgb"),
        wds.rename(image="png;jpg", cls="cls"),
        wds.to_tuple("image", "cls")
    )
    assert count_samples_tuple(ds) == 47
    image, cls = next(iter(ds))
    assert isinstance(image, np.ndarray), image
    assert isinstance(cls, int), type(cls)


def test_rgb8():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.shuffle(5),
        wds.decode("rgb8"),
        wds.rename(image="png;jpg", cls="cls"),
        wds.to_tuple("image", "cls")
    )
    assert count_samples_tuple(ds) == 47
    image, cls = next(iter(ds))
    assert isinstance(image, np.ndarray), type(image)
    assert image.dtype == np.uint8, image.dtype
    assert isinstance(cls, int), type(cls)


def test_pil():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.shuffle(5),
        wds.decode("pil"),
        wds.rename(image="png;jpg", cls="cls"),
        wds.to_tuple("image", "cls")
    )
    assert count_samples_tuple(ds) == 47
    image, cls = next(iter(ds))
    assert isinstance(image, PIL.Image.Image)


def test_raw():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.rename(image="png;jpg", cls="cls"),
        wds.to_tuple("image", "cls")
    )
    assert count_samples_tuple(ds) == 47
    image, cls = next(iter(ds))
    assert isinstance(image, bytes)
    assert isinstance(cls, bytes)


def IGNORE_test_only1():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.decode(only="cls"),
        wds.to_tuple("png;jpg", "cls")
    )
    assert count_samples_tuple(ds) == 47
    image, cls = next(iter(ds))
    assert isinstance(image, bytes)
    assert isinstance(cls, int)

    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.decode(only=["png", "jpg"]),
        wds.to_tuple("jpg;png", "cls")
    )
    assert count_samples_tuple(ds) == 47
    image, cls = next(iter(ds))
    assert isinstance(image, np.ndarray)
    assert isinstance(cls, bytes)


def test_gz():
    ds = wds.DataPipeline(
        wds.SimpleShardList(compressed),
        wds.tarfile_to_samples(),
        wds.decode(),
    )
    sample = next(iter(ds))
    print(sample)
    assert sample["txt.gz"] == "hello\n", sample


@pytest.mark.skip(reason="need to figure out unraisableexceptionwarning")
def test_rgb8_np_vs_torch():
    import warnings

    warnings.filterwarnings("error")
    ds = wds.WebDataset(local_data).decode("rgb8").to_tuple("png;jpg", "cls")
    image, cls = next(iter(ds))
    assert isinstance(image, np.ndarray), type(image)
    assert isinstance(cls, int), type(cls)
    ds = wds.WebDataset(local_data).decode("torchrgb8").to_tuple("png;jpg", "cls")
    image2, cls2 = next(iter(ds))
    assert isinstance(image2, torch.Tensor), type(image2)
    assert isinstance(cls, int), type(cls)
    assert (image == image2.permute(1, 2, 0).numpy()).all, (image.shape, image2.shape)
    assert cls == cls2


def test_float_np_vs_torch():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.decode("rgb"),
        wds.to_tuple("png;jpg", "cls")
    )
    image, cls = next(iter(ds))
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.decode("torchrgb"),
        wds.to_tuple("png;jpg", "cls")
    )
    image2, cls2 = next(iter(ds))
    assert (image == image2.permute(1, 2, 0).numpy()).all(), (image.shape, image2.shape)
    assert cls == cls2


# def test_associate():
#     with open("testdata/imagenet-extra.json") as stream:
#         extra_data = simplejson.load(stream)

#     def associate(key):
#         return dict(MY_EXTRA_DATA=extra_data[key])

#     ds = wds.WebDataset(local_data).associate(associate)

#     for sample in ds:
#         assert "MY_EXTRA_DATA" in sample.keys()
#         break


def test_tenbin():
    from webdataset import tenbin

    for d0 in [0, 1, 2, 10, 100, 1777]:
        for d1 in [0, 1, 2, 10, 100, 345]:
            for t in [np.uint8, np.float16, np.float32, np.float64]:
                a = np.random.normal(size=(d0, d1)).astype(t)
                a_encoded = tenbin.encode_buffer([a])
                (a_decoded,) = tenbin.decode_buffer(a_encoded)
                print(a.shape, a_decoded.shape)
                assert a.shape == a_decoded.shape
                assert a.dtype == a_decoded.dtype
                assert (a == a_decoded).all()


def test_tenbin_dec():
    ds = wds.DataPipeline(
        wds.SimpleShardList("testdata/tendata.tar"),
        wds.tarfile_to_samples(),
        wds.decode(),
        wds.to_tuple("ten"),
    )
    assert count_samples_tuple(ds) == 100
    for sample in ds:
        xs, ys = sample[0]
        assert xs.dtype == np.float64
        assert ys.dtype == np.float64
        assert xs.shape == (28, 28)
        assert ys.shape == (28, 28)


# def test_container_mp():
#     ds = wds.WebDataset("testdata/mpdata.tar", container="mp", decoder=None)
#     assert count_samples_tuple(ds) == 100
#     for sample in ds:
#         assert isinstance(sample, dict)
#         assert set(sample.keys()) == set("__key__ x y".split()), sample


# def test_container_ten():
#     ds = wds.WebDataset("testdata/tendata.tar", container="ten", decoder=None)
#     assert count_samples_tuple(ds) == 100
#     for xs, ys in ds:
#         assert xs.dtype == np.float64
#         assert ys.dtype == np.float64
#         assert xs.shape == (28, 28)
#         assert ys.shape == (28, 28)


def test_dataloader():
    import torch

    ds = wds.DataPipeline(
        wds.SimpleShardList(remote_loc + remote_shards),
        wds.tarfile_to_samples(),
        wds.decode("torchrgb"),
        wds.to_tuple("jpg;png", "json"),
    )
    dl = torch.utils.data.DataLoader(ds, num_workers=4)
    assert count_samples_tuple(dl, n=100) == 100


def IGNORE_test_multimode():
    import torch

    urls = [local_data] * 8
    nsamples = 47 * 8

    shardlist = wds.PytorchShardList(
        urls, verbose=True, epoch_shuffle=True, shuffle=True
    )
    os.environ["WDS_EPOCH"] = "7"
    ds = wds.WebDataset(shardlist)
    dl = torch.utils.data.DataLoader(ds, num_workers=4)
    count = count_samples_tuple(dl)
    assert count == nsamples, count
    del os.environ["WDS_EPOCH"]

    shardlist = wds.PytorchShardList(urls, verbose=True, split_by_worker=False)
    ds = wds.WebDataset(shardlist)
    dl = torch.utils.data.DataLoader(ds, num_workers=4)
    count = count_samples_tuple(dl)
    assert count == 4 * nsamples, count

    shardlist = shardlists.ResampledShards(urls)
    ds = wds.WebDataset(shardlist).slice(170)
    dl = torch.utils.data.DataLoader(ds, num_workers=4)
    count = count_samples_tuple(dl)
    assert count == 170 * 4, count


def test_decode_handlers():
    def mydecoder(data):
        return PIL.Image.open(io.BytesIO(data)).resize((128, 128))

    ds = wds.DataPipeline(
        wds.SimpleShardList(remote_loc + remote_shards),
        wds.tarfile_to_samples(),
        wds.decode(
            wds.handle_extension("jpg", mydecoder),
            wds.handle_extension("png", mydecoder),
        ),
        wds.to_tuple("jpg;png", "json")
    )

    for sample in ds:
        assert isinstance(sample[0], PIL.Image.Image)
        break


def test_decoder():
    def mydecoder(key, sample):
        return len(sample)

    ds = wds.DataPipeline(
        wds.SimpleShardList(remote_loc + remote_shards),
        wds.tarfile_to_samples(),
        wds.decode(mydecoder),
        wds.to_tuple("jpg;png", "json")
    )

    for sample in ds:
        assert isinstance(sample[0], int)
        break


def test_pipe():
    ds = wds.DataPipeline(
        wds.SimpleShardList(f"pipe:curl -s '{remote_loc}{remote_shards}'"),
        wds.tarfile_to_samples(),
        wds.shuffle(100),
        wds.to_tuple("jpg;png", "json")
    )
    assert count_samples_tuple(ds, n=10) == 10


def test_torchvision():
    import torch
    from torchvision import transforms

    normalize = transforms.Normalize(
        mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
    )
    preproc = transforms.Compose(
        [
            transforms.RandomResizedCrop(224),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            normalize,
        ]
    )
    ds = wds.DataPipeline(
        wds.SimpleShardList(f"pipe:curl -s '{remote_loc}{remote_shards}'"),
        wds.tarfile_to_samples(),
        wds.decode("pil"),
        wds.to_tuple("jpg;png", "json"),
        wds.map_tuple(preproc, None),
    )
    for sample in ds:
        assert isinstance(sample[0], torch.Tensor), type(sample[0])
        assert tuple(sample[0].size()) == (3, 224, 224), sample[0].size()
        assert isinstance(sample[1], list), type(sample[1])
        break


def test_batched():
    import torch
    from torchvision import transforms

    normalize = transforms.Normalize(
        mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
    )
    preproc = transforms.Compose(
        [
            transforms.RandomResizedCrop(224),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            normalize,
        ]
    )
    ds = wds.DataPipeline(
        wds.SimpleShardList(f"pipe:curl -s '{remote_loc}{remote_shards}'"),
        wds.tarfile_to_samples(),
        wds.decode("pil"),
        wds.to_tuple("jpg;png", "json"),
        wds.map_tuple(preproc, None),
        wds.batched(7),
    )
    for sample in ds:
        assert isinstance(sample[0], torch.Tensor), type(sample[0])
        assert tuple(sample[0].size()) == (7, 3, 224, 224), sample[0].size()
        assert isinstance(sample[1], list), type(sample[1])
        break
    pickle.dumps(ds)


def test_unbatched():
    import torch
    from torchvision import transforms

    normalize = transforms.Normalize(
        mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
    )
    preproc = transforms.Compose(
        [
            transforms.RandomResizedCrop(224),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            normalize,
        ]
    )
    ds = wds.DataPipeline(
        wds.SimpleShardList(remote_loc + remote_shards),
        wds.tarfile_to_samples(),
        wds.decode("pil"),
        wds.to_tuple("jpg;png", "json"),
        wds.map_tuple(preproc, None),
        wds.batched(7),
        wds.unbatched(),
    )
    for sample in ds:
        assert isinstance(sample[0], torch.Tensor), type(sample[0])
        assert tuple(sample[0].size()) == (3, 224, 224), sample[0].size()
        assert isinstance(sample[1], list), type(sample[1])
        break
    pickle.dumps(ds)


def test_chopped():
    from torchvision import datasets

    ds = datasets.FakeData(size=100)
    cds = eds.ChoppedDataset(ds, 20)
    assert count_samples_tuple(cds, n=500) == 20

    ds = datasets.FakeData(size=100)
    cds = eds.ChoppedDataset(ds, 250)
    assert count_samples_tuple(cds, n=500) == 250

    ds = datasets.FakeData(size=100)
    cds = eds.ChoppedDataset(ds, 77)
    assert count_samples_tuple(cds, n=500) == 77

    ds = datasets.FakeData(size=100)
    cds = eds.ChoppedDataset(ds, 250)
    assert count_samples_tuple(cds, n=500) == 250

    ds = datasets.FakeData(size=100)
    cds = eds.ChoppedDataset(ds, 250)
    assert count_samples_tuple(cds, n=500) == 250


def test_with_epoch():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
    )
    for _ in range(10):
        assert count_samples_tuple(ds) == 47
    be = wds.with_epoch(ds, 193)
    for _ in range(10):
        assert count_samples_tuple(be) == 193
    be = wds.with_epoch(ds, 2)
    for _ in range(10):
        assert count_samples_tuple(be) == 2


def test_repeat():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
    )
    ds = wds.repeatedly(ds, 2)
    assert count_samples_tuple(ds) == 47 * 2


def test_repeat2():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.tarfile_to_samples(),
        wds.to_tuple("cls"),
        wds.batched(2),
    )
    ds = wds.with_epoch(ds, 20)
    assert count_samples_tuple(ds) == 20


def test_webloader():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.split_by_worker,
        wds.tarfile_to_samples(),
        wds.to_tuple("png;jpg", "cls"),
    )
    dl = DataLoader(ds, num_workers=4, batch_size=3)
    nsamples = count_samples_tuple(dl)
    assert nsamples == (47 + 2) // 3, nsamples


def test_webloader2():
    ds = wds.DataPipeline(
        wds.SimpleShardList(local_data),
        wds.split_by_worker,
        wds.tarfile_to_samples(),
        wds.to_tuple("png;jpg", "cls"),
    )
    dl = wds.DataPipeline(
        DataLoader(ds, num_workers=4, batch_size=3, drop_last=True),
        wds.unbatched(),
    )
    nsamples = count_samples_tuple(dl)
    assert nsamples == 45, nsamples
