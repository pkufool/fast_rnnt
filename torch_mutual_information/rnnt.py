import os

import k2
import torch
from torch import Tensor
from typing import Tuple, Optional
from . mutual_information import mutual_information_recursion, joint_mutual_information_recursion



def get_rnnt_logprobs(lm: Tensor,
                      am: Tensor,
                      symbols: Tensor,
                      termination_symbol: int) -> Tuple[Tensor, Tensor]:
    """
    Reduces RNN-T problem (the simple case, where joiner network is just addition),
    to a compact, standard form that can then be given
    (with boundaries) to mutual_information_recursion().  This function is called from
    rnnt_loss_simple(), but may be useful for other purposes.

    Args:
         lm:  Language model part of un-normalized logprobs of symbols, to be added to
              acoustic model part before normalizing.  Of shape:
                 [B][S+1][C]
              where B is the batch size, S is the maximum sequence length of
              the symbol sequence, possibly including the EOS symbol; and
              C is size of the symbol vocabulary, including the termination/next-frame
              symbol.
              Conceptually, lm[b][s] is a vector of length [C] representing the
              "language model" part of the un-normalized logprobs of symbols,
              given all symbols *earlier than* s in the sequence.  The reason
              we still need this for position S is that we may still be emitting
              the termination/next-frame symbol at this point.
         am:  Acoustic-model part of un-normalized logprobs of symbols, to be added
              to language-model part before normalizing.  Of shape:
                 [B][T][C]
              where B is the batch size, T is the maximum sequence length of
              the acoustic sequences (in frames); and C is size of the symbol
              vocabulary, including the termination/next-frame symbol.  It reflects
              the "acoustic" part of the probability of any given symbol appearing
              next on this frame.
          symbols: A LongTensor of shape [B][S], containing the symbols at each position
              of the sequence, possibly including EOS
          termination_symbol: The identity of the termination symbol, must be
               in {0..C-1}
    Returns: (px, py) (the names are quite arbitrary).
              px: logprobs, of shape [B][S][T+1]
              py: logprobs, of shape [B][S+1][T]
          in the recursion:
             p[b,0,0] = 0.0
             p[b,s,t] = log_add(p[b,s-1,t] + px[b,s-1,t],
                                p[b,s,t-1] + py[b,s,t-1])
          .. where p[b][s][t] is the "joint score" of the pair of subsequences of
          length s and t respectively.  px[b][s][t] represents the probability of
          extending the subsequences of length (s,t) by one in the s direction,
          given the particular symbol, and py[b][s][t] represents the probability
          of extending the subsequences of length (s,t) by one in the t direction,
          i.e. of emitting the termination/next-frame symbol.

          px[:,:,T] equals -infinity, meaning on the "one-past-the-last" frame
          we cannot emit any symbols.  This is simply a way of incorporating
          the probability of the termination symbol on the last frame.
    """
    assert lm.ndim== 3 and am.ndim == 3 and lm.shape[0] == am.shape[0] and lm.shape[2] == am.shape[2]
    (B, T, C) = am.shape
    S = lm.shape[1] - 1
    assert symbols.shape == (B, S)

    # subtracting am_max and lm_max is to ensure the probs are in a good range to do exp()
    # without causing underflow or overflow.
    am_max, _ = torch.max(am, dim=2, keepdim=True)  # am_max: [B][T][1]
    lm_max, _ = torch.max(lm, dim=2, keepdim=True)  # lm_max: [B][S+1][1]
    am_probs = (am - am_max).exp()
    lm_probs = (lm - lm_max).exp()
    # normalizers: [B][S+1][T]
    normalizers = (torch.matmul(lm_probs, am_probs.transpose(1, 2)) + 1.0e-20).log()

    # add lm_max and am_max to normalizers, to make it as if we had not
    # subtracted am_max and lm_max above.
    normalizers = normalizers + lm_max + am_max.transpose(1, 2)  # [B][S+1][T]

    # px is the probs of the actual symbols..
    px_am = torch.gather(am.unsqueeze(1).expand(B, S, T, C), dim=3,
                         index=symbols.reshape(B, S, 1, 1).expand(B, S, T, 1)).squeeze(-1) # [B][S][T]
    px_am = torch.cat((px_am,
                       torch.full((B, S, 1), float('-inf'),
                                  device=px_am.device, dtype=px_am.dtype)),
                      dim=2)  # now: [B][S][T+1], index [:,:,T] has -inf..

    px_lm = torch.gather(lm[:,:S], dim=2, index=symbols.unsqueeze(-1)) # [B][S][1]

    px = px_am + px_lm  # [B][S][T+1], last slice indexed [:,:,T] is -inf
    px[:,:,:T] -= normalizers[:,:S,:] # px: [B][S][T+1]

    # py is the probs of termination symbols, of shape [B][S+1][T]
    py_am = am[:,:,termination_symbol].unsqueeze(1) # [B][1][T]
    py_lm = lm[:,:,termination_symbol].unsqueeze(2) # [B][S+1][1]
    py = py_am + py_lm - normalizers

    return (px, py)


def rnnt_loss_simple(lm: Tensor,
                     am: Tensor,
                     symbols: Tensor,
                     termination_symbol: int,
                     boundary: Tensor = None) -> Tensor:
    """
    A simple case of the RNN-T loss, where the 'joiner' network is just addition.
    Returns total loss value.

    Args:
     lm: language-model part of unnormalized log-probs of symbols, with shape
        (B, S+1, C), i.e. batch, symbol_seq_len+1, num_classes
     am: acoustic-model part of unnormalized log-probs of symbols, with shape
       (B, T, C), i.e. batch, frame, num_classes
     symbols: the symbol sequences, a LongTensor of shape [B][S], and elements in {0..C-1}.
     termination_symbol: the termination symbol, with 0 <= termination_symbol < C
     boundary: a LongTensor of shape [B, 4] with elements interpreted as
        [begin_symbol, begin_frame, end_symbol, end_frame] that is treated as [0, 0, S, T]
        if boundary is not supplied.
        Most likely you will want begin_symbol and begin_frame to be zero.
   Returns:
      a Tensor of shape (B,), containing the total RNN-T loss values for each element
      of the batch (like log-probs of sequences).
    """
    px, py = get_rnnt_logprobs(lm, am, symbols, termination_symbol)
    return mutual_information_recursion(px, py, boundary)


def get_rnnt_logprobs_joint(joint: Tensor,
                            symbols: Tensor,
                            termination_symbol: int) -> Tuple[Tensor, Tensor]:
    """
    Reduces RNN-T problem to a compact, standard form that can then be given
    (with boundaries) to mutual_information_recursion().  This function is called from
    rnnt_loss().

    Args:
      joint: The output of joiner network, with shape
             (B, T, S + 1, C), i.e. batch, time_seq_len, symbol_seq_len+1, num_classes
      symbols: A LongTensor of shape [B][S], containing the symbols at each position
          of the sequence, possibly including EOS
      termination_symbol: The identity of the termination symbol, must be
           in {0..C-1}
    Returns: (px, py) (the names are quite arbitrary).
              px: logprobs, of shape [B][S][T+1]
              py: logprobs, of shape [B][S+1][T]
          in the recursion:
             p[b,0,0] = 0.0
             p[b,s,t] = log_add(p[b,s-1,t] + px[b,s-1,t],
                                p[b,s,t-1] + py[b,s,t-1])
          .. where p[b][s][t] is the "joint score" of the pair of subsequences of
          length s and t respectively.  px[b][s][t] represents the probability of
          extending the subsequences of length (s,t) by one in the s direction,
          given the particular symbol, and py[b][s][t] represents the probability
          of extending the subsequences of length (s,t) by one in the t direction,
          i.e. of emitting the termination/next-frame symbol.

          px[:,:,T] equals -infinity, meaning on the "one-past-the-last" frame
          we cannot emit any symbols.  This is simply a way of incorporating
          the probability of the termination symbol on the last frame.
    """
    assert joint.ndim == 4
    (B, T, S1, C) = joint.shape
    S = S1 - 1
    assert symbols.shape == (B, S)

    max_value = torch.max(joint)
    normalizers = (joint - max_value)
    normalizers = torch.logsumexp(normalizers, dim=3)
    normalizers += max_value
    normalizers = normalizers.permute((0,2,1))

    px = torch.gather(joint, dim=3, index=symbols.reshape(B, 1, S, 1).expand(B, T, S, 1)).squeeze(-1)
    px = px.permute((0,2,1))
    px = torch.cat((px,
                    torch.full((B, S, 1), float('-inf'),
                            device=px.device, dtype=px.dtype)),
                    dim=2)  # now: [B][S][T+1], index [:,:,T] has -inf..
    px[:,:,:T] -= normalizers[:,:S,:]

    py = joint[:,:,:,termination_symbol].permute((0,2,1)).clone() # [B][S+1][T]
    py -= normalizers
    px = px.contiguous()
    py = py.contiguous()

    return (px, py)


def rnnt_loss(joint: Tensor,
             symbols: Tensor,
             termination_symbol: int,
             boundary: Tensor = None) -> Tensor:
    """
    A normal RNN-T loss, which uses a 'joiner' network output as input, i.e. a 4 dimensions tensor.

    Args:
      joint: The output of joiner network, with shape
             (B, T, S + 1, C), i.e. batch, time_seq_len, symbol_seq_len+1, num_classes
     symbols: The symbol sequences, a LongTensor of shape [B][S], and elements in {0..C-1}.
              termination_symbol: the termination symbol, with 0 <= termination_symbol < C
     termination_symbol: The termination symbol, with 0 <= termination_symbol < C
     boundary: a LongTensor of shape [B, 4] with elements interpreted as
        [begin_symbol, begin_frame, end_symbol, end_frame] that is treated as [0, 0, S, T]
        if boundary is not supplied.
        Most likely you will want begin_symbol and begin_frame to be zero.

    Returns:
      A Tensor of shape (B,), containing the total RNN-T loss values for each element
      of the batch (like log-probs of sequences).
    """
    px, py = get_rnnt_logprobs_joint(joint, symbols, termination_symbol)
    return mutual_information_recursion(px, py, boundary)


def adjust_pruning_lower_bound(s_begin: torch.Tensor, s_range: int) -> torch.Tensor:
    """
    Adjust s_begin (pruning lower bound) to make it satisfied the following constrains

      - monotonic increasing, i.e. s_begin[i] <= s_begin[i + 1]
      - start with symbol 0 at first frame.
      - s_begin[i + 1] - s_begin[i] < s_range, whicn means that we can't skip any symbols.

    To make it monotonic increasing, we can `monotonic_lower_bound` function in k2, which
    guarantee `s_begin[i] <= s_begin[i + 1]`. The main idea is: traverse the array in reverse
    order and update the elements by `min_value = min(a_begin[i], current_min_value)`,
    the initial `min_value` set to `inf`.

    The method we used to realize `s_begin[i + 1] - s_begin[i] < s_range` constrain is a little
    tricky. We first transform `s_begin` with `s_begin = -(s_begin - (s_range - 1) * torch.arange(0,T))`
    then we make the transformed `s_begin` monotonic increasing, after that, we transform back
    `s_begin` with the same formula as the previous transformation. The idea is: if we want to make
    `s_begin[i + 1] - s_begin[i] < s_range` we only need to make `-(s_begin[i] - i * (s_range - 1))` a
    non-decreasing array. Proof:

      -(s_begin[i] - i * (s_range - 1)) <= -(s_begin[i + 1] - (i + 1) * (s_range - 1))
                            -s_begin[i] <= -s_begin[i + 1] + (i + 1) * (s_range - 1) - i * (s_range - 1)
                            -s_begin[i] <= -s_begin[i + 1] + s_range - 1
            s_begin[i + 1] - s_begin[i] <= s_range - 1
            s_begin[i + 1] - s_begin[i] < s_range

    The above transformation can not guarantee the start symbol to be 0, so we have to make all the
    elements that less than 0 to 0 before transforming back the `s_begin`.
    """
    # s_begin (B, T)
    (B, T) = s_begin.shape
    # TODO: Implements torch.int64 version of k2.monotonic_lower_bound
    s_begin = k2.monotonic_lower_bound(s_begin.to(torch.int32))
    # do the magic transformation
    s_begin = -(s_begin - (s_range - 1) * torch.arange(0, T))
    # make the transformed tensor to be non-decreasing
    s_begin = k2.monotonic_lower_bound(s_begin.to(torch.int32))
    # make start symbol to be zero.
    s_begin = torch.where(s_begin < 0, 0, s_begin.to(torch.int64))
    # do the magic transformation again to recover s_begin
    s_begin = -(s_begin - (s_range - 1) * torch.arange(0, T))
    return s_begin


def get_pruning_ranges(px_grad: torch.Tensor, py_grad: torch.Tensor, s_range: int) -> torch.Tensor:
    """
    Get the pruning ranges for normal rnnt loss according to the grad of rnnt_loss_simple.

    Args:
      px_grad: The gradient of px, see docs in mutual_information_recursion for more details of px.
      py_grad: The gradient of py, see docs in mutual_information_recursion for more details of py.
      s_range: How many symbols to keep for each frame.

    Returns:
      A tensor contains the kept symbols id for each frame, with shape (B, T, s_range).
    """
    (B, S, T1) = px_grad.shape
    T = T1 - 1;
    assert py_grad.shape == (B, S + 1, T)
    assert s_range <= S

    px_pad = torch.zeros((B, 1, T + 1), dtype=px_grad.dtype, device=px_grad.device)
    py_pad = torch.zeros((B, S + 1, 1), dtype=py_grad.dtype, device=py_grad.device)
    tot_grad = torch.cat((px_grad, px_pad), dim=1) + torch.cat((py_grad, py_pad), dim=2) # (B, S + 1, T + 1)
    tot_grad = torch.cat((torch.zeros((B, 1, T+1), dtype=tot_grad.dtype, device=tot_grad.device), tot_grad), dim=1)
    tot_grad = torch.cumsum(tot_grad, dim=1)[:,:S+1,:]
    diff_grad = tot_grad[:,s_range:,:] - tot_grad[:, 0:-s_range,:]
    s_begin = torch.argmax(diff_grad, dim=1)
    s_begin = s_begin[:,:T]
    s_begin = adjust_pruning_lower_bound(s_begin, s_range)
    ranges = s_begin.reshape((B, T, 1)).expand((B, T, s_range)) + torch.arange(s_range)
    return ranges


def pruning(am: torch.Tensor,
            lm: torch.Tensor,
            ranges: torch.Tensor
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Pruning the output of encoder(am) output and prediction(lm) output of RNNT.

    Args:
      am: The encoder output, with shape (B, T, C)
      lm: The prediction network output, with shape (B, S + 1, C)
      ranges: A tensor keeping the symbol ids for each frame that we want to keep.

    Returns:
      Return the pruned am and lm with shape (B, T, S_range)
    """
    # am (B, T, C)
    # lm (B, S + 1, C)
    # ranges (B, T, S_range)
    assert ranges.shape[0] == am.shape[0] and ranges.shape[0] == lm.shape[0]
    assert am.shape[1] == ranges.shape[1]
    (B, T, S_range) = ranges.shape
    (B, S1, C) = lm.shape
    S = S1 - 1

    # (B, T, S_range, C)
    am_pruning = am.unsqueeze(2).expand((B, T, S_range, C))

    # (B, T, S_range, C)
    lm_pruning = torch.gather(lm.unsqueeze(1).expand((B, T, S + 1, C)), dim=2,
                              index=ranges.reshape((B, T, S_range, 1)).expand((B, T, S_range, C)))

    return am_pruning, lm_pruning


def roll_by_shifts(src: torch.Tensor, shifts: torch.LongTensor):
    """
    Roll tensor with different shifts.

    Note:
      We assume the src is a 3 dimensions tensor and roll the last dimension.

    Example:

      >>> src = torch.arange(15).reshape((1,3,5))
      >>> src
      tensor([[[ 0,  1,  2,  3,  4],
               [ 5,  6,  7,  8,  9],
               [10, 11, 12, 13, 14]]])
      >>> shift = torch.tensor([[1, 2, 3]])
      >>> shift
      tensor([[1, 2, 3]])
      >>> roll_by_shifts(src, shift)
      tensor([[[ 4,  0,  1,  2,  3],
               [ 8,  9,  5,  6,  7],
               [12, 13, 14, 10, 11]]])
    """
    assert src.dim() == 3
    (B, T, S) = src.shape
    assert shifts.shape == (B, T)

    index = torch.arange(S).view((1, S)).repeat((T, 1)).repeat((B, 1, 1))
    index = (index - shifts.reshape(B, T, 1)) % S
    return torch.gather(src, 2, index)


def get_rnnt_logprobs_pruning(joint: Tensor,
                              symbols: Tensor,
                              ranges: Tensor,
                              termination_symbol: int) -> Tuple[Tensor, Tensor]:
    """
    Construct px, py for mutual_information_recursion with pruned output.

    Args:
      joint: The pruned output of joint network, with shape (B, T, s_range, C)
      symbols: The symbol sequences, a LongTensor of shape [B][S], and elements in {0..C-1}.
               termination_symbol: the termination symbol, with 0 <= termination_symbol < C
      ranges: A tensor keeping the symbol ids for each frame that we want to keep.
      termination_symbol: The termination symbol, with 0 <= termination_symbol < C

    Returns:
      Return the px (B, S, T + 1) and py (B, S + 1, T) needed by mutual_information_recursion.
    """
    # joint (B, T, S_range, C)
    # symbols (B, S)
    # ranges (B, T, S_range)
    assert joint.ndim == 4
    (B, T, S_range, C) = joint.shape
    assert ranges.shape == (B, T, S_range)
    (B, S) = symbols.shape

    max_value = torch.max(joint)
    normalizers = (joint - max_value)
    normalizers = torch.logsumexp(normalizers, dim=3)
    normalizers += max_value

    symbols_with_terminal = torch.cat((symbols, torch.tensor([termination_symbol] * B,
                      dtype=torch.int64, device=symbols.device).reshape((B, 1))), dim=1)
    # (B, T, S_range)
    pruning_symbols = torch.gather(symbols_with_terminal.unsqueeze(1).expand((B, T, S+1)), dim=2,
                                   index=ranges)

    # (B, T, S_range)
    px = torch.gather(joint, dim=3, index=pruning_symbols.reshape(B, T, S_range, 1)).squeeze(-1)
    px = px - normalizers

    px = torch.cat((px,
                    torch.full((B, T, S - S_range), float('-inf'),
                        device=px.device, dtype=px.dtype)),
                    dim=2)  # (B, T, S) with index larger than s_range in dim 2 fill with -inf

    # (B, T, S) with index out of s_range in dim 2 fill with -inf
    px = roll_by_shifts(px, ranges[:,:,0])
    px = px.permute((0,2,1))
    px = torch.cat((px,
                    torch.full((B, S, 1), float('-inf'),
                            device=px.device, dtype=px.dtype)),
                    dim=2)  # now: [B][S][T+1], index [:,:,T] has -inf..

    py = joint[:,:,:,termination_symbol] # (B, T, S_range)
    py = py - normalizers

    py = torch.cat((py,
                    torch.full((B, T, S + 1 - S_range), float('-inf'),
                        device=py.device, dtype=py.dtype)),
                    dim=2)  # (B, T, S + 1) with index larger than s_range in dim 2 fill with -inf

    # (B, T, S + 1) with index out of s_range in dim 2 fill with -inf
    py = roll_by_shifts(py, ranges[:,:,0])
    # (B, S + 1, T)
    py = py.permute((0, 2, 1))

    px = px.contiguous()
    py = py.contiguous()

    return (px, py)


def pruning_rnnt_loss(joint: Tensor,
                      symbols: Tensor,
                      ranges: Tensor,
                      termination_symbol: int,
                      boundary: Tensor = None) -> Tensor:
    """
    A RNN-T loss with pruning, which uses a pruned 'joiner' network output as input,
    i.e. a 4 dimensions tensor with shape (B, T, s_range, C), s_range means the symbols
    number kept for each frame.

    Args:
      joint: The pruned output of joiner network, with shape
             (B, T, s_range, C), i.e. batch, time_seq_len, pruning_range, num_classes
     symbols: The symbol sequences, a LongTensor of shape [B][S], and elements in {0..C-1}.
              termination_symbol: the termination symbol, with 0 <= termination_symbol < C
     ranges: A tensor keeping the symbol ids for each frame that we want to keep.
     termination_symbol: The termination symbol, with 0 <= termination_symbol < C
     boundary: a LongTensor of shape [B, 4] with elements interpreted as
        [begin_symbol, begin_frame, end_symbol, end_frame] that is treated as [0, 0, S, T]
        if boundary is not supplied.
        Most likely you will want begin_symbol and begin_frame to be zero.

    Returns:
      A Tensor of shape (B,), containing the total RNN-T loss values for each element
      of the batch (like log-probs of sequences).
    """
    px, py = get_rnnt_logprobs_pruning(joint, symbols, ranges, termination_symbol)
    return mutual_information_recursion(px, py, boundary)



